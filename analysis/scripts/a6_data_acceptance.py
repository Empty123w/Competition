"""A6 交接数据验收 (B 侧独立核查; 不改 A 模型/不重训)。

核查 artifacts/handoff_b/ 三表: SHA256、行数、主键唯一、列结构、时间频率/时区、target_time。
并独立复算预测质量: MAE/RMSE/MAPE/MAPE_mf50/WAPE、90%区间覆盖率、上下界违反率、
低估比例、缺失率、低流量样本影响。

输出: analysis/reports/b/a6_data_acceptance.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HB = Path("artifacts/handoff_b")
OUT = Path("analysis/reports/b/a6_data_acceptance.json")
HORIZONS = [15, 30, 60]
ISSUE_FREQ = 5
MF = 50.0   # MAPE_mf 掩码阈值 (actual >= MF)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _check_file(name, manifest):
    p = HB / name
    spec = manifest["outputs"][name]
    df = pd.read_parquet(p)
    res = {"rows": int(len(df)), "cols": list(df.columns),
           "manifest_rows": spec["rows"], "rows_match": int(len(df)) == spec["rows"],
           "sha256": _sha(p), "manifest_sha256": spec["sha256"].upper(),
           "sha256_match": _sha(p) == spec["sha256"].upper(),
           "cols_match": list(df.columns) == spec["cols"]}
    return res, df


def _pk_unique(df):
    return bool(~df.duplicated(subset=["as_of", "horizon_minutes"]).any())


def _target_ok(df):
    return bool((df["target_time"] == df["as_of"] + pd.to_timedelta(df["horizon_minutes"], unit="m")).all())


def _freq_minutes(df):
    ts = df["as_of"].drop_duplicates().sort_values()
    d = ts.diff().dropna().dt.total_seconds() / 60.0
    return int(d.mode().iloc[0]) if len(d) else None


def _metrics(actual, pred, lower, upper):
    e = pred - actual
    n = len(actual)
    mape_mask = actual > 0
    mf_mask = actual >= MF
    return {
        "n": int(n),
        "MAE": round(float(np.mean(np.abs(e))), 4),
        "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 4),
        "MAPE": round(float(np.mean(np.abs(e[mape_mask]) / actual[mape_mask]) * 100), 3) if mape_mask.any() else None,
        "MAPE_floor1": round(float(np.mean(np.abs(e) / np.maximum(actual, 1.0)) * 100), 3),  # A 口径(mape_floor_kw=1.0)
        "MAPE_excl_zero": round(float(np.mean(np.abs(e[mape_mask]) / actual[mape_mask]) * 100), 3) if mape_mask.any() else None,
        "zero_flow_n": int((actual <= 0).sum()),
        "MAPE_mf50": round(float(np.mean(np.abs(e[mf_mask]) / actual[mf_mask]) * 100), 3) if mf_mask.any() else None,
        "WAPE": round(float(np.sum(np.abs(e)) / np.sum(actual) * 100), 3) if actual.sum() else None,
        "coverage_90": round(float(np.mean((actual >= lower) & (actual <= upper))), 4),
        "below_lower_rate": round(float(np.mean(actual < lower)), 4),
        "above_upper_rate": round(float(np.mean(actual > upper)), 4),
        "underestimate_rate": round(float(np.mean(actual > pred)), 4),
        "low_flow_share": round(float(np.mean(actual < MF)), 4),
    }


def main():
    manifest = json.loads((HB / "replay_manifest.json").read_text(encoding="utf-8"))
    rep = {"manifest": {"source_commit": manifest["source_commit"], "timezone": manifest["timezone"],
                        "target": manifest["target"], "primary_key": manifest["primary_key"],
                        "issue_frequency_minutes": manifest["issue_frequency_minutes"],
                        "horizons_minutes": manifest["horizons_minutes"],
                        "interval": manifest["interval"]}}
    files = {}
    for name in manifest["outputs"]:
        res, _ = _check_file(name, manifest)
        files[name] = res
    rep["file_checks"] = files

    dev = pd.read_parquet(HB / "forecast_dev_dispatch.parquet")
    val = pd.read_parquet(HB / "forecast_validation_dispatch.parquet")
    act = pd.read_parquet(HB / "forecast_scoring_actuals.parquet")

    struct = {}
    for nm, df in (("dev_dispatch", dev), ("val_dispatch", val), ("scoring_actuals", act)):
        key = ["as_of", "horizon_minutes"]
        struct[nm] = {"pk_unique": _pk_unique(df),
                      "target_time_ok": _target_ok(df) if "target_time" in df else None,
                      "issue_freq_minutes": _freq_minutes(df),
                      "horizons": sorted(df["horizon_minutes"].unique().tolist()),
                      "tz_naive": str(df["as_of"].dtype).startswith("datetime64") and df["as_of"].dt.tz is None,
                      "min_as_of": str(df["as_of"].min()), "max_as_of": str(df["as_of"].max())}
    rep["structure"] = struct

    # 质量: dispatch 与 scoring 关联
    q = {}
    for split, disp, tag in (("dev", dev, "dev"), ("validation", val, "valid")):
        m = disp.merge(
            act[act["split"].astype(str) == tag][
                ["as_of", "horizon_minutes", "actual_flow", "actual_available"]],
            on=["as_of", "horizon_minutes"], how="left")
        miss_rate = float(m["actual_available"].isna().sum() + (~m["actual_available"].fillna(False)).sum()) / len(m)
        m = m[m["actual_available"].fillna(False)].copy()
        per_h = {}
        for h in HORIZONS:
            g = m[m["horizon_minutes"] == h]
            per_h[f"{h}min"] = _metrics(g["actual_flow"].to_numpy(), g["predicted_flow"].to_numpy(),
                                        g["lower_bound"].to_numpy(), g["upper_bound"].to_numpy())
        q[split] = {"missing_rate": round(miss_rate, 4),
                    "overall": _metrics(m["actual_flow"].to_numpy(), m["predicted_flow"].to_numpy(),
                                        m["lower_bound"].to_numpy(), m["upper_bound"].to_numpy()),
                    "by_horizon": per_h}
    rep["quality"] = q

    # 与 A 报告对比
    rep["a_reported_vs_recomputed"] = {
        "valid_15min": {"A_MAPE": 34.097, "B_MAPE": q["validation"]["by_horizon"]["15min"]["MAPE"],
                        "A_MAE": 7.445, "B_MAE": q["validation"]["by_horizon"]["15min"]["MAE"],
                        "A_coverage": 0.2937, "B_coverage": q["validation"]["by_horizon"]["15min"]["coverage_90"]},
        "valid_30min": {"A_MAPE": 34.56, "B_MAPE": q["validation"]["by_horizon"]["30min"]["MAPE"],
                        "A_coverage": 0.2609, "B_coverage": q["validation"]["by_horizon"]["30min"]["coverage_90"]},
        "valid_60min": {"A_MAPE": 35.025, "B_MAPE": q["validation"]["by_horizon"]["60min"]["MAPE"],
                        "A_coverage": 0.2194, "B_coverage": q["validation"]["by_horizon"]["60min"]["coverage_90"]},
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
