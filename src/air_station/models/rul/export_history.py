"""历史 RUL 迁移估计导出 (阶段 A6, §11)。

- 复用现有模拟退化模型, 不重训;
- 真实遥测按设备重采样到 6 小时节点, 只取 (T-6h, T] 且只用当前及过去;
- 需 8 个完整 6 小时历史节点才输出有效迁移估计;
- OOD 检查(以模拟训练集稳健分位区间为参考); 负值截断并降级;
- 只用于软约束演示, 不代表真实剩余寿命。
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.rul import simulator

NODE = "6h"
WINDOW = 8
FIXED = {"validation_mode": "simulation", "usage_context": "real_telemetry_transfer",
         "input_origin": "real_telemetry", "model_training_origin": "simulated",
         "sampling_frequency": "6 hours", "max_staleness_hours": 6}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


def _sim_ranges(seq_path: str, feats: list[str]) -> dict:
    s = pd.read_parquet(seq_path)
    return {f: (float(s[f].quantile(0.005)), float(s[f].quantile(0.995))) for f in feats if f in s.columns}


def export(processed_dir: str = "data/processed",
           model_dir: str = "artifacts/models",
           sequences_path: str = "data/simulated/rul_sequences.parquet",
           out_path: str = "artifacts/metrics/rul_history.parquet",
           report_path: str = "analysis/reports/phaseA6_replay/rul_history_report.md") -> dict:
    import joblib
    import json
    meta = json.loads((Path(model_dir) / "rul_meta.json").read_text(encoding="utf-8"))
    feats = list(meta["features"])
    models = {n: joblib.load(Path(model_dir) / f"rul_{n}.pkl") for n in ("median", "lower", "upper")}
    msha = {n: _sha(Path(model_dir) / f"rul_{n}.pkl") for n in ("median", "lower", "upper")}
    version = meta.get("model_type", "GradientBoostingRegressor")
    ranges = _sim_ranges(sequences_path, feats)

    import pyarrow.parquet as pq
    p = Path(processed_dir) / "health_features.parquet"
    if not p.exists():
        raise FileNotFoundError(f"[rul-history] 缺少 {p}")
    use = [c for c in ["time", "device_id", "running", "vib1_current", "vib2_current",
                       "vib3_current", "discharge_temp_c", "oil_tank_c",
                       "current_proxy_a", "unit_flow_power"] if c in pq.read_schema(p).names]
    df = pd.read_parquet(p, columns=use); df["time"] = pd.to_datetime(df["time"])

    rows = []
    for dev, sub in df.groupby("device_id", observed=True):
        sub = sub.sort_values("time").set_index("time")
        run = sub[sub["running"].astype(bool)]
        g = run.resample(NODE, label="right", closed="left")
        last = g.last()
        cnt = g.size()
        src = run.index.to_series().resample(NODE, label="right", closed="left").max()
        last["_src"] = src.reindex(last.index).values
        last["_cnt"] = cnt.reindex(last.index).values
        # 滚动特征(仅在 6h 节点, 只用过去)
        vib_mean = last[["vib1_current", "vib2_current", "vib3_current"]].mean(axis=1)
        last["vib_roll"] = vib_mean.rolling(WINDOW, min_periods=WINDOW).mean()
        last["temp_roll"] = last["discharge_temp_c"].rolling(WINDOW, min_periods=WINDOW).mean()
        last["vib_delta"] = vib_mean.diff()
        for t, r in last.iterrows():
            if r[feats].isna().any():
                continue
            x = r[feats].astype(float).to_numpy().reshape(1, -1)
            med = float(models["median"]["model"].predict(x)[0])
            lo = float(models["lower"]["model"].predict(x)[0])
            hi = float(models["upper"]["model"].predict(x)[0])
            lo, hi = min(lo, med, hi), max(lo, med, hi)
            clipped = bool(med < 0 or lo < 0)
            med, lo, hi = max(med, 0.0), max(lo, 0.0), max(hi, 0.0)
            ood = sum(1 for f in feats if f in ranges and not (ranges[f][0] <= r[f] <= ranges[f][1]))
            src_t = pd.Timestamp(r["_src"])
            stale = (pd.Timestamp(t) - src_t).total_seconds() / 3600.0
            cov = float(min(r["_cnt"] / 360.0, 1.0))
            ok = (not clipped) and (ood == 0) and (stale <= FIXED["max_staleness_hours"]) and cov >= 0.5
            rows.append({
                "time": pd.Timestamp(t), "device_id": int(dev), "source_time": src_t,
                "input_data_cutoff_time": src_t, "estimated_rul_hours": round(med, 1),
                "lower_bound_hours": round(lo, 1), "upper_bound_hours": round(hi, 1),
                "method": version, "validation_mode": FIXED["validation_mode"],
                "usage_context": FIXED["usage_context"], "input_origin": FIXED["input_origin"],
                "model_training_origin": FIXED["model_training_origin"], "model_version": f"rul-sim-v1",
                "median_model_sha256": msha["median"], "lower_model_sha256": msha["lower"],
                "upper_model_sha256": msha["upper"], "quality_valid": bool(ok),
                "quality_reason": "ok" if ok else ("out_of_distribution" if ood else
                                  ("prediction_clipped" if clipped else
                                   ("stale" if stale > FIXED["max_staleness_hours"] else "window_insufficient"))),
                "out_of_distribution": bool(ood > 0), "ood_feature_count": int(ood),
                "prediction_clipped": clipped, "stale_hours": round(stale, 2),
                "fresh_prediction": True, "window_sample_count": int(r["_cnt"]),
                "window_coverage_rate": round(cov, 4),
                "valid_until": pd.Timestamp(t) + pd.Timedelta(hours=6),
                "max_staleness_hours": FIXED["max_staleness_hours"],
            })
    out = pd.DataFrame(rows)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    # 断言
    assert out.empty or (out["lower_bound_hours"] <= out["estimated_rul_hours"]).all()
    assert out.empty or (out["estimated_rul_hours"] <= out["upper_bound_hours"]).all()
    _write_report(Path(report_path), out)
    return {"rows": int(len(out)), "path": out_path}


def _write_report(path: Path, out: pd.DataFrame) -> None:
    lines = ["# 阶段 A6 历史 RUL 迁移估计报告", "",
             "> 模拟退化模型在真实历史遥测上的**迁移估计**，用于软约束演示，**不代表真实剩余寿命**。", "",
             f"- validation_mode: simulation；usage_context: real_telemetry_transfer",
             f"- 粒度：6 小时；窗口 (T-6h, T]；需 8 个完整历史节点",
             f"- 输出行数：{len(out)}；有效：{int(out['quality_valid'].sum()) if len(out) else 0}", "",
             "## 质量原因分布", ""]
    if len(out):
        for k, v in out["quality_reason"].value_counts().to_dict().items():
            lines.append(f"- {k}: {v}")
    lines += ["", "## B 使用限制", "",
              "- 仅作健康惩罚/调度软约束；不触发停机，不放宽任何安全约束；过期或无效时回退健康等级。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="历史 RUL 迁移导出")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--model-dir", default="artifacts/models")
    args = ap.parse_args()
    r = export(args.processed_dir, args.model_dir)
    print(f"[OK] rul_history rows={r['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
