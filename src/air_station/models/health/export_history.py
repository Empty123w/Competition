"""历史健康评分导出 (阶段 A6, §10)。

- 小时粒度; 专家规则只用当前及过去测量值;
- 同设备同加载区间的正常基准用 **shift(1)+expanding**(评分行不进自己的基准);
- 基准样本仅取过去"运行 + 非缺失 + 未触发专家报警"的记录;
- 前 14 天为基准预热, 首个评分为 2026-03-26;
- 停机时刻 health_score=null, quality_valid=false, quality_reason=not_running;
- 不修改冻结 health_json()。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.health import rules, scoring, service

WARMUP_END = "2026-03-26 00:00:00"
MIN_BASELINE_SAMPLES = 30
STD_FLOOR = 1e-3


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


def _hourly(processed_dir: str) -> pd.DataFrame:
    th = rules.load_thresholds()
    df = service._load_features(processed_dir, th)      # 已过滤 running==1? -> 需保留停机行
    return df


def _load_all(processed_dir: str) -> pd.DataFrame:
    path = Path(processed_dir) / "health_features.parquet"
    if not path.exists():
        raise FileNotFoundError(f"[health-history] 缺少 {path}")
    cols = pd.read_parquet(path, columns=["time", "device_id"]).columns  # noqa
    import pyarrow.parquet as pq
    avail = list(pq.read_schema(path).names)
    need = set(["time", "device_id", "running", "load_ratio"])
    for r in rules.load_thresholds()["expert_rules"]:
        need.add(r["feature"])
        need.add(f"trend{rules.load_thresholds()['trend']['window']}_{r['feature']}")
    need |= set(rules.load_thresholds()["anomaly"]["dev_features"])
    for dv in rules.load_thresholds()["anomaly"]["dev_features"]:
        need.add(dv.replace("dev_", ""))   # base feature for baseline
    use = sorted(c for c in need if c in avail)
    df = pd.read_parquet(path, columns=use)
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values(["device_id", "time"]).reset_index(drop=True)


def _hourly_last(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for dev, sub in df.groupby("device_id", observed=True):
        sub = sub.set_index("time")
        src = sub.index.to_series().resample("1h", label="right", closed="left").max()
        agg = sub.resample("1h", label="right", closed="left").last()
        agg["device_id"] = dev
        agg["source_time"] = src.reindex(agg.index).values
        out.append(agg.reset_index())
    return pd.concat(out, ignore_index=True)


def _causal_baselines(hourly: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """为每个 dev 基础特征计算 (device,band) 的严格过去均值/标准差。"""
    bands = {"low": (0.0, 0.30), "mid": (0.30, 0.70), "high": (0.70, 10.0)}
    lr = hourly["load_ratio"].astype(float)
    hourly["load_band"] = pd.cut(lr, bins=[-1, 0.30, 0.70, 1e9], labels=["low", "mid", "high"])

    dev_feats = thresholds["anomaly"]["dev_features"]
    rule_max = rules.rule_penalties(hourly, thresholds["expert_rules"]).max(axis=1)
    hourly["_alarm"] = rule_max.gt(0).to_numpy()

    for dv in dev_feats:
        base = dv.replace("dev_", "")
        if base not in hourly.columns:
            continue
        # 合格基准样本: 运行 & 非空 & 无报警
        q = hourly[hourly["running"].astype(bool) & hourly[base].notna() & ~hourly["_alarm"]].copy()
        q = q.sort_values(["device_id", "load_band", "time"])
        g = q.groupby(["device_id", "load_band"], observed=True)
        x = q[base].astype(float)
        csum = g[base].cumsum()
        csq = (x ** 2).groupby([q["device_id"], q["load_band"]], observed=True).cumsum()
        cnt = g.cumcount()
        mean_past = (csum - x) / cnt.replace(0, np.nan)
        var_past = ((csq - x ** 2) / cnt.replace(0, np.nan)) - mean_past ** 2
        std_past = np.sqrt(var_past.clip(lower=STD_FLOOR ** 2))
        bdict = q[["device_id", "load_band", "time"]].copy()
        bdict["_mean"] = mean_past.to_numpy()
        bdict["_std"] = std_past.to_numpy()
        bdict["_cnt"] = cnt.to_numpy()
        bdict["_bl_time"] = bdict["time"]

        hourly = pd.merge_asof(
            hourly.sort_values("time"),
            bdict.sort_values("time"),
            on="time", by=["device_id", "load_band"],
            direction="backward", allow_exact_matches=False)
        hourly["_bl_time"] = hourly["_bl_time"].where(hourly["_cnt"].ge(MIN_BASELINE_SAMPLES))
        dev = (hourly[base].astype(float) - hourly["_mean"]) / hourly["_std"]
        hourly[dv] = np.where(hourly["running"].astype(bool) & hourly["_cnt"].ge(MIN_BASELINE_SAMPLES),
                              dev, np.nan)
        hourly["_baseline_cutoff"] = hourly["_bl_time"]
        hourly.drop(columns=["_mean", "_std", "_cnt", "_bl_time"], inplace=True, errors="ignore")
    return hourly


def export(config_path: str = "configs/health.yaml",
           thresholds_path: str = "configs/health_thresholds.yaml",
           processed_dir: str = "data/processed",
           out_path: str = "artifacts/metrics/health_history.parquet",
           report_path: str = "analysis/reports/phaseA6_replay/health_history_report.md") -> dict:
    thresholds = rules.load_thresholds(thresholds_path)
    cfg_sha = _sha(Path(thresholds_path))
    df = _load_all(processed_dir)
    hourly = _hourly_last(df)
    hourly = hourly[hourly["time"] >= pd.Timestamp(WARMUP_END)].reset_index(drop=True)
    hourly = _causal_baselines(hourly, thresholds)

    rows = []
    for _, r in hourly.iterrows():
        running = bool(r.get("running", False))
        base_valid = pd.notna(r.get("dev_vib1_current"))
        t = pd.Timestamp(r["time"])
        src = pd.Timestamp(r["source_time"]) if pd.notna(r.get("source_time")) else t
        rec = {"time": t, "device_id": int(r["device_id"]), "source_time": src,
               "input_data_cutoff_time": src, "baseline_cutoff_time": r.get("_baseline_cutoff"),
               "valid_until": t + pd.Timedelta(minutes=60), "max_staleness_minutes": 60,
               "validation_mode": "unsupervised_rolling", "config_sha256": cfg_sha}
        if not running:
            rec.update({"health_score": np.nan, "risk_level": None, "anomaly_score": np.nan,
                        "top_anomaly_factor": None, "trend_direction": None,
                        "quality_valid": False, "quality_reason": "not_running"})
        else:
            entry = service._device_entry(int(r["device_id"]), r, thresholds)
            rec.update({"health_score": entry["health_score"], "risk_level": entry["risk_level"],
                        "anomaly_score": entry["anomaly_score"],
                        "top_anomaly_factor": entry["top_anomaly_factor"],
                        "trend_direction": entry["trend_direction"],
                        "quality_valid": bool(base_valid),
                        "quality_reason": "ok" if base_valid else "baseline_insufficient"})
        rows.append(rec)
    out = pd.DataFrame(rows)
    cols = ["time", "device_id", "source_time", "input_data_cutoff_time", "health_score",
            "risk_level", "anomaly_score", "top_anomaly_factor", "trend_direction",
            "quality_valid", "quality_reason", "baseline_cutoff_time", "valid_until",
            "max_staleness_minutes", "validation_mode", "config_sha256"]
    out = out[cols]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)

    # 断言: 基准截止严格早于评分时间
    ok = out.loc[out["baseline_cutoff_time"].notna(), :]
    assert (pd.to_datetime(ok["baseline_cutoff_time"]) < ok["time"]).all()

    _write_report(Path(report_path), out)
    return {"rows": int(len(out)), "path": out_path}


def _write_report(path: Path, out: pd.DataFrame) -> None:
    ran = out[out["quality_valid"]]
    lines = ["# 阶段 A6 历史健康评分报告", "",
             "- 粒度：60 分钟；`validation_mode=unsupervised_rolling`",
             "- 正常基准：同设备同加载区间 `shift(1)+expanding`（评分行不入自身基准）",
             "- 风险等级/规则/偏差缩放取自 `configs/health_thresholds.yaml`",
             "- 停机：`health_score=null, quality_valid=false, quality_reason=not_running`",
             f"- 输出行数：{len(out)}；有效运行评分行：{len(ran)}", "",
             "## 分级分布（有效行）", ""]
    if len(ran):
        vc = ran["risk_level"].value_counts().to_dict()
        lines += [f"- {k}: {v}" for k, v in vc.items()]
    lines += ["", "> 本表为无监督滚动风险提示，不代表真实故障识别准确率。", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="历史健康评分导出")
    ap.add_argument("--processed-dir", default="data/processed")
    ap.add_argument("--config", default="configs/health.yaml")
    ap.add_argument("--thresholds", default="configs/health_thresholds.yaml")
    args = ap.parse_args()
    r = export(args.config, args.thresholds, args.processed_dir)
    print(f"[OK] health_history rows={r['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
