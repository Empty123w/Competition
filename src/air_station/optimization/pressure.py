"""B2 管网压力近似模型 (站级母管压力短时响应)。

依据任务书 V1.1 §6.3。

关键结论 (由数据实证, 见 pressure_report.md):
  - 母管压力近似随机游走 (lag-1 自相关 ≈ 0.99);
  - 压力变化与站级流量变化相关性很弱;
  - 因此本模块只提供**历史短时持续性**预测, **不声称**具备调度动作后的
    反事实压力预测能力; 优化后压力合格率只能作为仿真指标。

对外接口 (B3 调用): predict_pressure(), check_pressure_band()
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from air_station.optimization import contracts as C

DEFAULT_CONFIG = "configs/optimization.yaml"
DEFAULT_PRESSURE_MODEL = "artifacts/models/pressure_model.json"
DEFAULT_METRICS = "artifacts/metrics/pressure_metrics.json"
PRESSURE_MODEL_VERSION = "b2-pressure-v0.1"

FEATURES = ["current_pressure_mpa", "total_flow_m3_min", "flow_delta_m3_min", "running_count"]


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[pressure] 配置文件缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def build_station_frame(processed_dir: str | Path = "data/processed") -> pd.DataFrame:
    base = Path(processed_dir)
    st_path = base / "station_timeseries.parquet"
    dev_path = base / "device_timeseries.parquet"
    if not st_path.exists():
        raise FileNotFoundError(f"[pressure] 站级数据缺失: {st_path}")
    if not dev_path.exists():
        raise FileNotFoundError(f"[pressure] 设备数据缺失: {dev_path}")
    st = pd.read_parquet(st_path, columns=["time", "header_pressure_mpa", "total_flow_m3_min"])
    dev = pd.read_parquet(dev_path, columns=["time", "device_id", "running"])
    st["time"] = pd.to_datetime(st["time"])
    dev["time"] = pd.to_datetime(dev["time"])
    run_count = dev.groupby("time")["running"].sum().rename("running_count")
    df = st.merge(run_count, on="time", how="left").sort_values("time").reset_index(drop=True)
    df = df[df["header_pressure_mpa"] > 0].reset_index(drop=True)
    df["flow_delta_m3_min"] = df["total_flow_m3_min"].diff()
    df["next_pressure_mpa"] = df["header_pressure_mpa"].shift(-1)
    df["current_pressure_mpa"] = df["header_pressure_mpa"]
    df = df.dropna(subset=["current_pressure_mpa", "next_pressure_mpa",
                           "flow_delta_m3_min", "running_count"])
    return df.reset_index(drop=True)


def split_masks(times: pd.Series, cfg: dict) -> dict[str, np.ndarray]:
    sp = cfg["split"]
    t = pd.to_datetime(times)

    def m(seg):
        return ((t >= pd.Timestamp(seg["start"])) & (t <= pd.Timestamp(seg["end"]))).to_numpy()
    return {"train": m(sp["train"]), "valid": m(sp["valid"]), "test": m(sp["test"])}


def regression_metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2)) or 1.0
    return {"MAE": round(mae, 5), "RMSE": round(rmse, 5),
            "R2": round(1.0 - ss_res / ss_tot, 4), "n": int(len(y_true))}


def _fit_persistence(df: pd.DataFrame) -> dict:
    return {"type": "persistence"}


def _predict_persistence(p: dict, df: pd.DataFrame) -> np.ndarray:
    return df["current_pressure_mpa"].to_numpy(dtype=float)


def _fit_linear_dynamics(df: pd.DataFrame, features: list[str], y: np.ndarray) -> dict:
    X = df[features].to_numpy(dtype=float)
    A = np.hstack([X, np.ones((len(X), 1))])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return {"type": "linear_dynamics", "features": list(features),
            "coef": [float(v) for v in coef[:-1]], "intercept": float(coef[-1]),
            "feature_means": [float(np.nanmean(df[f])) for f in features]}


def _predict_linear_dynamics(p: dict, df: pd.DataFrame) -> np.ndarray:
    X = df[p["features"]].to_numpy(dtype=float)
    idx = ~np.isfinite(X)
    if idx.any():
        X[idx] = np.take(np.array(p["feature_means"]), np.where(idx)[1])
    return X @ np.array(p["coef"]) + p["intercept"]


def predict_model(spec: dict, df: pd.DataFrame) -> np.ndarray:
    if spec["type"] == "persistence":
        return _predict_persistence(spec, df)
    if spec["type"] == "linear_dynamics":
        return _predict_linear_dynamics(spec, df)
    raise ValueError(f"[pressure] 未知模型类型: {spec['type']}")


def train_pressure(processed_dir="data/processed", config_path=DEFAULT_CONFIG,
                   model_path=DEFAULT_PRESSURE_MODEL, metrics_path=DEFAULT_METRICS) -> dict:
    cfg = load_config(config_path)
    df = build_station_frame(processed_dir)
    m = split_masks(df["time"], cfg)
    tr, va, te = df[m["train"]], df[m["valid"]], df[m["test"]]
    y_tr, y_va, y_te = (tr["next_pressure_mpa"].to_numpy(), va["next_pressure_mpa"].to_numpy(),
                        te["next_pressure_mpa"].to_numpy())

    persistence = _fit_persistence(tr)
    dyn = _fit_linear_dynamics(tr, FEATURES, y_tr)
    candidates = {"persistence": persistence, "linear_dynamics": dyn}
    valid_metrics = {k: regression_metrics(y_va, predict_model(s, va)) for k, s in candidates.items()}
    metric = cfg["pressure"].get("selection_metric", "MAE")
    chosen = min(valid_metrics, key=lambda n: valid_metrics[n][metric])

    frozen_df = pd.concat([tr, va], ignore_index=True)
    if chosen == "persistence":
        frozen = _fit_persistence(frozen_df)
    else:
        frozen = _fit_linear_dynamics(frozen_df, FEATURES,
                                      frozen_df["next_pressure_mpa"].to_numpy())

    def _m(seg):
        return regression_metrics(seg["next_pressure_mpa"].to_numpy(), predict_model(frozen, seg))
    metrics = {"train": _m(tr), "valid": _m(va), "test": _m(te)}
    resid = (te["next_pressure_mpa"].to_numpy() - predict_model(frozen, te)) if len(te) else np.array([])
    residual_std = float(np.std(resid)) if len(resid) else None

    # 参考带 (待确认)
    band = cfg["pressure"].get("reference_band_mpa", [0.55, 0.66])
    p = df["current_pressure_mpa"]
    within = float(((p >= band[0]) & (p <= band[1])).mean())

    # 反事实能力证据
    p_all = df["current_pressure_mpa"]
    evidence = {
        "lag1_autocorr": round(float(p_all.autocorr(1)), 4),
        "dp_flow_delta_corr": round(float(df[["flow_delta_m3_min", "next_pressure_mpa"]]
                                          .assign(dp=lambda x: x.next_pressure_mpa - p_all)
                                          [["dp", "flow_delta_m3_min"]].corr().iloc[0, 1]), 4),
        "p_std_mpa": round(float(p_all.std()), 5),
        "within_reference_band_share": round(within, 4),
        "reference_band_mpa": [float(band[0]), float(band[1])],
    }

    artifact = {
        "model_version": cfg["pressure"].get("model_version", PRESSURE_MODEL_VERSION),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validation_mode": "simulation",
        "method": chosen,
        "params": frozen,
        "features": FEATURES,
        "horizon_minutes": int(cfg["pressure"].get("horizon_minutes", 1)),
        "sample_counts": {"train": int(len(tr)), "valid": int(len(va)), "test": int(len(te))},
        "candidate_valid_metrics": valid_metrics,
        "metrics": metrics,
        "residual_std_mpa": residual_std,
        "uncertainty_1.96sigma_mpa": round(1.96 * residual_std, 5) if residual_std else None,
        "evidence": evidence,
        "capability_statement": {
            "historical_short_horizon": "supported (persistence-dominated)",
            "counterfactual_after_dispatch": "NOT supported by current data",
            "reason": "压力近似随机游走, 变化与流量/运行台数相关性弱, 无法可靠识别调度动作对压力的因果影响",
        },
        "notes": "工程近似; 压力合格带为待确认示例, 非厂家安全标准",
    }
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    Path(model_path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(metrics_path).parent.mkdir(parents=True, exist_ok=True)
    Path(metrics_path).write_text(json.dumps(
        {k: artifact[k] for k in ("model_version", "method", "metrics", "candidate_valid_metrics",
                                  "residual_std_mpa", "uncertainty_1.96sigma_mpa", "evidence",
                                  "capability_statement", "sample_counts", "validation_mode")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


# ------------------------------------------------------------------ #
# B3 运行时接口
# ------------------------------------------------------------------ #
def _load_artifact(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[pressure] 压力模型缺失: {p} (先运行 train_pressure)")
    return json.loads(p.read_text(encoding="utf-8"))


def _pct(v):
    if v is None or not np.isfinite(float(v)):
        return None
    return round(float(v) * 100.0, 2)


def predict_pressure(current_pressure_mpa, *, total_flow_m3_min=None,
                     flow_delta_m3_min=None, running_count=None,
                     model_path: str | Path = DEFAULT_PRESSURE_MODEL) -> dict:
    """预测下一分钟母管压力 (B3 调用)。

    返回 status ∈ {success, INVALID_INPUT, MODEL_UNAVAILABLE}; 含不确定性与能力声明。
    """
    if current_pressure_mpa is None or not np.isfinite(float(current_pressure_mpa)):
        return {"status": C.RC_INVALID_INPUT, "message": "current_pressure_mpa 缺失或非有限"}
    p = float(current_pressure_mpa)
    try:
        art = _load_artifact(model_path)
    except FileNotFoundError as e:
        return {"status": C.RC_MODEL_UNAVAILABLE, "message": str(e)}
    row = {"current_pressure_mpa": p, "total_flow_m3_min": total_flow_m3_min,
           "flow_delta_m3_min": flow_delta_m3_min, "running_count": running_count}
    frame = pd.DataFrame([row])
    for f in FEATURES:
        if f not in frame:
            frame[f] = np.nan
    pred = float(predict_model(art["params"], frame)[0])
    unc = art.get("uncertainty_1.96sigma_mpa")
    band = art["evidence"]["reference_band_mpa"]
    return {
        "status": "success",
        "current_pressure_mpa": round(p, 4),
        "predicted_pressure_mpa": round(pred, 4),
        "uncertainty_1.96sigma_mpa": unc,
        "method": art["method"],
        "horizon_minutes": art["horizon_minutes"],
        "in_reference_band": bool(band[0] <= pred <= band[1]),
        "reference_band_mpa": band,
        "model_version": art["model_version"],
        "validation_mode": art["validation_mode"],
        "capability": art["capability_statement"],
        "caveats": [
            "压力近似随机游走, 该预测为历史短时持续性, 不代表调度动作后的压力",
            "压力合格带为待确认示例, 非厂家安全标准",
        ],
    }


def check_pressure_band(pressure_mpa, band=None) -> dict:
    if pressure_mpa is None or not np.isfinite(float(pressure_mpa)):
        return {"status": C.RC_INVALID_INPUT, "pass": None}
    b = band or (0.55, 0.66)
    p = float(pressure_mpa)
    return {"status": "success", "pass": bool(b[0] <= p <= b[1]),
            "margin_mpa": round(min(p - b[0], b[1] - p), 4), "band_mpa": [float(b[0]), float(b[1])]}


if __name__ == "__main__":
    art = train_pressure()
    print(art["method"], "test:", art["metrics"]["test"])
    print("evidence:", art["evidence"])
    print("capability:", art["capability_statement"])
