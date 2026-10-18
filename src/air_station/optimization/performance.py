"""B2 单机性能模型 (功率—流量)。

目标: 为 4#、5# 分别建立 `power_kw = f(flow, ...)` 近似模型, 供 B3 优化器估算电耗。
依据任务书 V1.1 §6.1/§6.2。

原则:
  - 只用 A 的标准数据 data/processed/device_timeseries.parquet (+ 站级环境);
  - 按运行状态、设备编号、质量标记筛选; 停机/零流量/超额单独标记;
  - 4# 与 5# 分别拟合, 不假设效率相同;
  - 沿用 A 的时间切分; 验证集选型, 测试集仅冻结评估;
  - rated / observed_max / validated_operating_max 分别存储, 不把历史超额当持续安全容量;
  - 无厂家性能曲线, 模型为**工程近似**, validation_mode 标 simulation。

对外接口 (B3 调用): estimate_power(), power_domain()
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from air_station.optimization import contracts as C

DEFAULT_CONFIG = "configs/optimization.yaml"
DEFAULT_POWER_MODEL = "artifacts/models/power_model.json"
DEFAULT_METRICS = "artifacts/metrics/performance_metrics.json"
POWER_MODEL_VERSION = "b2-power-v0.1"
_METHOD_LABEL = {
    "linear": "LinearRegression(flow)",
    "poly2": "Polynomial2(flow)",
    "piecewise": "PiecewiseLinear(flow,1knot)",
    "multivar": "LinearRegression(flow,pressure,igv,ambient)",
}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[performance] 配置文件缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ #
# 数据装载与清洗
# ------------------------------------------------------------------ #
def load_samples(processed_dir: str | Path = "data/processed") -> pd.DataFrame:
    base = Path(processed_dir)
    dev_path = base / "device_timeseries.parquet"
    st_path = base / "station_timeseries.parquet"
    if not dev_path.exists():
        raise FileNotFoundError(f"[performance] 设备数据缺失: {dev_path}")
    if not st_path.exists():
        raise FileNotFoundError(f"[performance] 站级数据缺失: {st_path}")
    dev = pd.read_parquet(dev_path, columns=[
        "time", "device_id", "running", "flow_m3_min", "power_kw", "load_ratio",
        "discharge_pressure_mpa", "igv_percent", "bov_closed_percent", "quality_valid"])
    st = pd.read_parquet(st_path, columns=[
        "time", "header_pressure_mpa", "total_flow_m3_min", "station_temperature_c",
        "station_humidity_percent"])
    dev["time"] = pd.to_datetime(dev["time"])
    st["time"] = pd.to_datetime(st["time"])
    dev["device_id"] = dev["device_id"].map(C.normalize_device_id)
    df = dev.merge(st, on="time", how="left")
    df["ambient_temp_c"] = df["station_temperature_c"].where(df["station_temperature_c"] > 0)
    return df.sort_values(["device_id", "time"]).reset_index(drop=True)


def split_masks(times: pd.Series, cfg: dict) -> dict[str, np.ndarray]:
    sp = cfg["split"]
    t = pd.to_datetime(times)

    def m(seg):
        return ((t >= pd.Timestamp(seg["start"])) & (t <= pd.Timestamp(seg["end"]))).to_numpy()
    return {"train": m(sp["train"]), "valid": m(sp["valid"]), "test": m(sp["test"])}


def screen_samples(df: pd.DataFrame, device_id: str, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """筛出可用于拟合的运行样本, 并统计被排除类别 (V1.1 §6.1/§6.2)。"""
    pc = cfg["performance"]
    lo = float(pc["fit_flow_lo_m3min"])
    hi = float(pc["fit_flow_hi_m3min"])
    sub = df[df["device_id"] == device_id].copy()
    stats = {"total": int(len(sub))}
    running = sub[sub["running"].astype(bool)]
    stats["stopped"] = int(len(sub) - len(running))
    off_zero = running[running["flow_m3_min"] <= 0]
    stats["running_zero_flow"] = int(len(off_zero))
    flowing = running[running["flow_m3_min"] > 0]
    stats["below_validated_min"] = int((flowing["flow_m3_min"] < lo).sum())
    stats["above_validated_max"] = int((flowing["flow_m3_min"] > hi).sum())
    stats["nonfinite"] = int((~np.isfinite(flowing["power_kw"]) | ~np.isfinite(flowing["flow_m3_min"])).sum())
    fit = flowing[(flowing["flow_m3_min"] >= lo) & (flowing["flow_m3_min"] <= hi)]
    fit = fit[np.isfinite(fit["power_kw"]) & np.isfinite(fit["flow_m3_min"]) & (fit["power_kw"] > 0)]
    stats["used"] = int(len(fit))
    # 超额工况统计 (调节范围之上, 仅报告不用于拟合)
    ov = flowing[flowing["flow_m3_min"] > hi]
    stats["overload_share_running"] = round(float((flowing["flow_m3_min"] > hi).mean()), 4)
    stats["overload_max_flow"] = round(float(flowing["flow_m3_min"].max()), 2) if len(flowing) else None
    return fit.reset_index(drop=True), stats


# ------------------------------------------------------------------ #
# 候选模型 (纯参数 dict, 便于序列化与 B3 复用)
# ------------------------------------------------------------------ #
def _lsq(A: np.ndarray, y: np.ndarray) -> np.ndarray:
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return coef


def _fit_linear_1d(x: np.ndarray, y: np.ndarray) -> dict:
    a, b = _lsq(np.vstack([x, np.ones_like(x)]).T, y)
    return {"type": "linear", "slope": float(a), "intercept": float(b)}


def _predict_linear_1d(p: dict, x: np.ndarray) -> np.ndarray:
    return p["slope"] * x + p["intercept"]


def _fit_poly2(x: np.ndarray, y: np.ndarray) -> dict:
    c = np.polyfit(x, y, 2)
    return {"type": "poly2", "coef": [float(v) for v in c]}


def _predict_poly2(p: dict, x: np.ndarray) -> np.ndarray:
    return np.polyval(p["coef"], x)


def _fit_piecewise(x: np.ndarray, y: np.ndarray, knot: float) -> Optional[dict]:
    left, right = x <= knot, x > knot
    if left.sum() < 20 or right.sum() < 20:
        return None
    return {"type": "piecewise", "knot": float(knot),
            "left": _fit_linear_1d(x[left], y[left]),
            "right": _fit_linear_1d(x[right], y[right])}


def _predict_piecewise(p: dict, x: np.ndarray) -> np.ndarray:
    out = np.where(x <= p["knot"], _predict_linear_1d(p["left"], x),
                   _predict_linear_1d(p["right"], x))
    return out


def _fit_multivar(df: pd.DataFrame, features: list[str], y: np.ndarray) -> dict:
    X = df[features].to_numpy(dtype=float)
    A = np.hstack([X, np.ones((len(X), 1))])
    coef = _lsq(A, y)
    return {"type": "multivar", "features": list(features),
            "coef": [float(v) for v in coef[:-1]], "intercept": float(coef[-1]),
            "feature_means": [float(np.nanmean(df[f].to_numpy(dtype=float))) for f in features]}


def _predict_multivar(p: dict, df: pd.DataFrame) -> np.ndarray:
    X = df[p["features"]].to_numpy(dtype=float)
    means = np.array(p["feature_means"], dtype=float)
    idx = ~np.isfinite(X)
    if idx.any():
        X[idx] = np.take(means, np.where(idx)[1])
    return X @ np.array(p["coef"]) + p["intercept"]


def predict_model(spec: dict, df: pd.DataFrame) -> np.ndarray:
    t = spec["type"]
    if t in ("linear",):
        return _predict_linear_1d(spec, df["flow_m3_min"].to_numpy(dtype=float))
    if t == "poly2":
        return _predict_poly2(spec, df["flow_m3_min"].to_numpy(dtype=float))
    if t == "piecewise":
        return _predict_piecewise(spec, df["flow_m3_min"].to_numpy(dtype=float))
    if t == "multivar":
        return _predict_multivar(spec, df)
    raise ValueError(f"[performance] 未知模型类型: {t}")


# ------------------------------------------------------------------ #
# 指标
# ------------------------------------------------------------------ #
def regression_metrics(y_true, y_pred, mape_floor: float = 1.0) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    denom = np.maximum(np.abs(y_true), mape_floor)
    mape = float(np.mean(np.abs(err) / denom) * 100.0)
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2)) or 1.0
    return {"MAE": round(mae, 3), "RMSE": round(rmse, 3),
            "MAPE": round(mape, 3), "R2": round(1.0 - ss_res / ss_tot, 4), "n": int(len(y_true))}


# ------------------------------------------------------------------ #
# 训练 / 选型 / 冻结评估
# ------------------------------------------------------------------ #
def _fit_candidates(fit_df: pd.DataFrame, features: list[str], cfg: dict) -> dict[str, dict]:
    x = fit_df["flow_m3_min"].to_numpy(dtype=float)
    y = fit_df["power_kw"].to_numpy(dtype=float)
    models = {"linear": _fit_linear_1d(x, y), "poly2": _fit_poly2(x, y),
              "multivar": _fit_multivar(fit_df, features, y)}
    # 分段: 在流量分位数上搜索拐点
    best = None
    for q in np.linspace(0.3, 0.75, 10):
        knot = float(np.quantile(x, q))
        pw = _fit_piecewise(x, y, knot)
        if pw is None:
            continue
        pred = _predict_piecewise(pw, x)
        r = regression_metrics(y, pred)
        if best is None or r["MAE"] < best[1]["MAE"]:
            best = (pw, r)
    if best is not None:
        models["piecewise"] = best[0]
    return models


def train_device(df: pd.DataFrame, device_id: str, cfg: dict) -> dict:
    pc = cfg["performance"]
    features = list(pc["feature_columns"])
    fit_df, screen_stats = screen_samples(df, device_id, cfg)
    masks = split_masks(fit_df["time"], cfg)
    tr, va, te = fit_df[masks["train"]], fit_df[masks["valid"]], fit_df[masks["test"]]

    candidates = _fit_candidates(tr, features, cfg)
    valid_metrics = {}
    for name, spec in candidates.items():
        valid_metrics[name] = regression_metrics(va["power_kw"].to_numpy(),
                                                 predict_model(spec, va))
    metric = pc.get("selection_metric", "MAE")
    chosen = min(valid_metrics, key=lambda n: valid_metrics[n][metric])

    # 冻结: 用 train+valid 重拟合选中规格, 测试集只评估一次
    frozen_df = pd.concat([tr, va], ignore_index=True)
    frozen_candidates = _fit_candidates(frozen_df, features, cfg)
    frozen = frozen_candidates[chosen]

    def _m(seg):
        return regression_metrics(seg["power_kw"].to_numpy(), predict_model(frozen, seg)) if len(seg) else None
    metrics = {"train": _m(tr), "valid": _m(va), "test": _m(te)}

    resid = te["power_kw"].to_numpy() - predict_model(frozen, te) if len(te) else np.array([])
    return {
        "device_id": device_id,
        "method": _METHOD_LABEL.get(chosen, chosen),
        "method_key": chosen,
        "params": frozen,
        "features": features,
        "fit_flow_range_m3min": [float(pc["fit_flow_lo_m3min"]), float(pc["fit_flow_hi_m3min"])],
        "validated_operating_max_m3min": float(pc["validated_operating_max_m3min"]),
        "rated_capacity_m3min": float(pc["rated_capacity_m3min"]),
        "observed_max_m3min": screen_stats.get("overload_max_flow"),
        "sample_counts": {**screen_stats,
                          "train": int(len(tr)), "valid": int(len(va)), "test": int(len(te))},
        "candidate_valid_metrics": valid_metrics,
        "metrics": metrics,
        "residual": {"mean": round(float(np.mean(resid)), 3) if len(resid) else None,
                     "std": round(float(np.std(resid)), 3) if len(resid) else None,
                     "p95_abs": round(float(np.percentile(np.abs(resid), 95)), 3) if len(resid) else None},
        "validation_mode": "simulation",
        "model_version": pc.get("model_version", POWER_MODEL_VERSION),
        "notes": "工程近似模型, 无厂家性能曲线; 不用于真实设备控制",
    }


def train_performance(processed_dir="data/processed", config_path=DEFAULT_CONFIG,
                      model_path=DEFAULT_POWER_MODEL, metrics_path=DEFAULT_METRICS) -> dict:
    cfg = load_config(config_path)
    df = load_samples(processed_dir)
    devices = {}
    for did in C.DEVICE_IDS:
        if (df["device_id"] == did).any():
            devices[did] = train_device(df, did, cfg)
    artifact = {"model_version": cfg["performance"].get("model_version", POWER_MODEL_VERSION),
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "validation_mode": "simulation",
                "split": cfg["split"], "devices": devices}
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    Path(model_path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = {"model_version": artifact["model_version"], "validation_mode": "simulation",
               "devices": {k: {"method": v["method"], "metrics": v["metrics"],
                               "sample_counts": v["sample_counts"],
                               "validated_operating_max_m3min": v["validated_operating_max_m3min"],
                               "observed_max_m3min": v["observed_max_m3min"]}
                           for k, v in devices.items()}}
    Path(metrics_path).parent.mkdir(parents=True, exist_ok=True)
    Path(metrics_path).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


# ------------------------------------------------------------------ #
# B3 运行时接口
# ------------------------------------------------------------------ #
def _load_artifact(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[performance] 功率模型缺失: {p} (先运行 train_performance)")
    return json.loads(p.read_text(encoding="utf-8"))


def power_domain(device_id: str, model_path: str | Path = DEFAULT_POWER_MODEL) -> dict:
    did = C.normalize_device_id(device_id)
    art = _load_artifact(model_path)
    dev = art["devices"].get(did)
    if dev is None:
        return {"status": C.RC_INVALID_INPUT, "device_id": did, "message": "无该设备模型"}
    lo, hi = dev["fit_flow_range_m3min"]
    return {"status": "success", "device_id": did, "min_flow_m3min": lo, "max_flow_m3min": hi,
            "rated_capacity_m3min": dev["rated_capacity_m3min"],
            "observed_max_m3min": dev["observed_max_m3min"], "method": dev["method"],
            "validation_mode": dev["validation_mode"]}


def estimate_power(device_id: str, flow_m3_min, *,
                   discharge_pressure_mpa=None, igv_percent=None, ambient_temp_c=None,
                   model_path: str | Path = DEFAULT_POWER_MODEL) -> dict:
    """估算单机功率 (B3 调用)。

    返回 status ∈ {success, INVALID_INPUT, MODEL_UNAVAILABLE}。
    flow<=0 视为停机, 功率为 0; 负值/非有限值 -> INVALID_INPUT。
    """
    did = C.normalize_device_id(device_id)
    if did not in C.DEVICE_IDS:
        return {"status": C.RC_INVALID_INPUT, "device_id": str(device_id),
                "message": f"真实设备仅 {C.DEVICE_IDS}"}
    if flow_m3_min is None or not np.isfinite(float(flow_m3_min)):
        return {"status": C.RC_INVALID_INPUT, "device_id": did, "message": "flow 缺失或非有限"}
    flow = float(flow_m3_min)
    if flow < 0:
        return {"status": C.RC_INVALID_INPUT, "device_id": did, "message": "flow 为负"}
    if flow == 0:
        return {"status": "success", "device_id": did, "flow_m3_min": 0.0,
                "estimated_power_kw": 0.0, "specific_power_kw_per_m3min": None,
                "in_validated_range": True, "extrapolated": False, "method": "off",
                "model_version": None, "validation_mode": "simulation", "warnings": []}
    try:
        art = _load_artifact(model_path)
    except FileNotFoundError as e:
        return {"status": C.RC_MODEL_UNAVAILABLE, "device_id": did, "message": str(e)}
    dev = art["devices"].get(did)
    if dev is None:
        return {"status": C.RC_MODEL_UNAVAILABLE, "device_id": did, "message": "无该设备模型"}

    lo, hi = dev["fit_flow_range_m3min"]
    row = {"flow_m3_min": flow,
           "discharge_pressure_mpa": discharge_pressure_mpa,
           "igv_percent": igv_percent,
           "ambient_temp_c": ambient_temp_c}
    frame = pd.DataFrame([row])
    for f in dev["features"]:
        if f not in frame:
            frame[f] = np.nan
    power = float(predict_model(dev["params"], frame)[0])
    warnings = []
    extrapolated = not (lo <= flow <= hi)
    if extrapolated:
        warnings.append(f"流量 {flow:.1f} 超出拟合区间 [{lo}, {hi}] m3/min, 属外推")
    if flow > dev["observed_max_m3min"] or flow > dev["validated_operating_max_m3min"]:
        warnings.append("流量超过已验证持续上限, 不作为真实可持续安全容量")
    return {
        "status": "success", "device_id": did, "flow_m3_min": round(flow, 2),
        "estimated_power_kw": round(power, 2),
        "specific_power_kw_per_m3min": round(power / flow, 4),
        "in_validated_range": bool(lo <= flow <= hi), "extrapolated": bool(extrapolated),
        "method": dev["method"], "model_version": dev["model_version"],
        "validation_mode": dev["validation_mode"], "warnings": warnings,
    }


def estimate_total_power(actions, model_path: str | Path = DEFAULT_POWER_MODEL) -> dict:
    """对一组 {device_id, flow_m3_min} 动作求和 (B3 便捷接口)。"""
    total, per, ok = 0.0, [], True
    for a in actions:
        r = estimate_power(a["device_id"], a.get("flow_m3_min"), model_path=model_path)
        per.append(r)
        if r["status"] == "success":
            total += float(r["estimated_power_kw"])
        else:
            ok = False
    return {"status": "success" if ok else C.RC_MODEL_UNAVAILABLE,
            "estimated_power_kw": round(total, 2), "devices": per}


if __name__ == "__main__":
    art = train_performance()
    for did, d in art["devices"].items():
        print(did, d["method"], "test:", d["metrics"]["test"])
