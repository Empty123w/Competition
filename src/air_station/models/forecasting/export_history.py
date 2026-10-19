"""历史回放导出 (阶段 A6)。

唯一正式生成入口: 产出 B 侧两张调度表 + 一张评分真值表 + 审计表 + manifest + 报告。
- 调度表严格六列, 不含 actual_flow;
- 评分真值表 join 不到真实流量时 actual_flow=NaN 且 actual_available=False(不 drop);
- 审计表通过 fold_id 映射到独立元数据字典, 不重复存哈希。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.forecasting import rolling_backtest as RB
from air_station.models.forecasting import rolling_features as RF

TIME = "time"
DISPATCH_COLS = ["as_of", "target_time", "horizon_minutes",
                 "predicted_flow", "lower_bound", "upper_bound"]


def _write_parquet(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return _sha(path)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest().upper()


def _metrics(y, p, lo, hi, min_flow: float = 50.0) -> dict:
    y = np.asarray(y, float); p = np.asarray(p, float)
    m = np.isfinite(y) & np.isfinite(p)
    if m.sum() == 0:
        return {"MAE": None, "RMSE": None, "MAPE": None, "MAPE_mf50": None,
                "WAPE": None, "coverage": None, "n": 0}
    e = y[m] - p[m]
    mf = m & (np.abs(y) >= min_flow)
    return {"MAE": round(float(np.mean(np.abs(e))), 3),
            "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "MAPE": round(float(np.mean(np.abs(e) / np.maximum(np.abs(y[m]), 1.0)) * 100), 3),
            "MAPE_mf50": round(float(np.mean(np.abs(y[mf] - p[mf]) / np.maximum(np.abs(y[mf]), 1.0)) * 100), 3) if mf.sum() else None,
            "WAPE": round(float(np.sum(np.abs(e)) / max(np.sum(np.abs(y[m])), 1.0) * 100), 3),
            "coverage": round(float(np.mean((y[m] >= np.asarray(lo, float)[m]) &
                                            (y[m] <= np.asarray(hi, float)[m]))), 4),
            "n": int(m.sum())}


def export(config_path: str = "configs/rolling_forecast.yaml") -> dict:
    cfg = RF.load_config(config_path)
    paths = cfg["paths"]
    handoff = Path(paths["handoff_dir"]); handoff.mkdir(parents=True, exist_ok=True)
    metrics_dir = Path(paths["metrics_dir"]); metrics_dir.mkdir(parents=True, exist_ok=True)
    report_dir = Path(paths["report_dir"]); report_dir.mkdir(parents=True, exist_ok=True)

    r = RB.run(config_path, resume=True)
    preds = r["preds"].copy()
    audit = r["audit"].copy()
    if preds.empty:
        raise RuntimeError("无预测输出")

    preds["as_of"] = pd.to_datetime(preds["as_of"])
    preds["target_time"] = pd.to_datetime(preds["target_time"])
    audit["as_of"] = pd.to_datetime(audit["as_of"])
    audit["target_time"] = pd.to_datetime(audit["target_time"])
    dev_start = pd.Timestamp(cfg["dev"]["start"]); dev_end = pd.Timestamp(cfg["dev"]["end"])
    valid_start = pd.Timestamp(cfg["valid"]["start"]); valid_end = pd.Timestamp(cfg["valid"]["end"])

    def _mask(df):
        is_dev = (df["as_of"] < valid_start) & (df["target_time"] <= dev_end)
        is_valid = (df["as_of"] >= valid_start) & (df["target_time"] <= valid_end)
        return is_dev, is_valid

    dm, vm = _mask(preds)
    preds = preds[dm | vm].reset_index(drop=True)
    adm, avm = _mask(audit)
    audit = audit[adm | avm].reset_index(drop=True)

    # 真实流量映射 (target_time -> actual)
    st = pd.read_parquet(Path(paths["processed_dir"]) / "station_timeseries.parquet")
    st[TIME] = pd.to_datetime(st[TIME])
    actual = st.set_index(TIME)["total_flow_m3_min"]
    preds["actual_flow"] = preds["target_time"].map(actual)
    preds["actual_available"] = preds["actual_flow"].notna()

    preds["split"] = np.where(preds["as_of"] < valid_start, "dev", "valid")

    # 调度表(六列, 不含 actual_flow)
    dev_disp = preds.loc[preds["split"] == "dev", DISPATCH_COLS].reset_index(drop=True)
    val_disp = preds.loc[preds["split"] == "valid", DISPATCH_COLS].reset_index(drop=True)
    scoring = preds.loc[:, ["split", "as_of", "target_time", "horizon_minutes",
                            "actual_flow", "actual_available"]].reset_index(drop=True)

    dispatch_dev = handoff / "forecast_dev_dispatch.parquet"
    dispatch_val = handoff / "forecast_validation_dispatch.parquet"
    scoring_p = handoff / "forecast_scoring_actuals.parquet"
    h_dev = _write_parquet(dev_disp, dispatch_dev)
    h_val = _write_parquet(val_disp, dispatch_val)
    h_sc = _write_parquet(scoring, scoring_p)

    # 审计表(不含大段 Hash) + 折级元数据字典
    audit.to_parquet(metrics_dir / "forecast_rolling_audit.parquet", index=False)
    (metrics_dir / "forecast_fold_metadata.json").write_text(
        json.dumps({"config_sha256": r["config_sha256"], "folds": r["fold_meta"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 指标(模型 vs lag_1 基线: 基线=as_of 时刻真实流量)
    preds["baseline_lag_1"] = preds["as_of"].map(actual)
    per = {}
    for split in ("dev", "valid"):
        sub = preds[preds["split"] == split]
        for h in cfg["horizons_minutes"]:
            s = sub[sub["horizon_minutes"] == h]
            per[f"{split}_{h}min"] = {
                "model": _metrics(s["actual_flow"], s["predicted_flow"], s["lower_bound"], s["upper_bound"]),
                "baseline_lag_1": _metrics(s["actual_flow"], s["baseline_lag_1"], s["baseline_lag_1"], s["baseline_lag_1"]),
            }

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_commit": _git_commit(),
        "timezone": cfg["timezone"], "timestamp_storage": cfg["timestamp_storage"],
        "source_sampling_frequency_minutes": cfg["source_sampling_frequency_minutes"],
        "issue_frequency_minutes": cfg["issue_frequency_minutes"],
        "horizons_minutes": cfg["horizons_minutes"],
        "target": {"type": cfg["target"]["type"], "unit": cfg["target"]["unit"],
                   "definition": "target_time = as_of + horizon_minutes; 瞬时点流量"},
        "primary_key": ["as_of", "horizon_minutes"],
        "interval": {"level": cfg["interval"]["level"], "method": "rolling calibration residual quantiles"},
        "outputs": {
            "forecast_dev_dispatch.parquet": {"sha256": h_dev, "rows": len(dev_disp), "cols": DISPATCH_COLS},
            "forecast_validation_dispatch.parquet": {"sha256": h_val, "rows": len(val_disp), "cols": DISPATCH_COLS},
            "forecast_scoring_actuals.parquet": {"sha256": h_sc, "rows": len(scoring),
                                                 "cols": ["split", "as_of", "target_time", "horizon_minutes", "actual_flow", "actual_available"]},
        },
        "actual_flow": {"note": "join 不到真实流量时置 NaN 且 actual_available=False; 不 drop",
                        "unit": cfg["target"]["unit"]},
        "folds": {"count": len(r["folds"]), "config_sha256": r["config_sha256"],
                  "meta_ref": "artifacts/metrics/forecast_fold_metadata.json"},
        "quality": {"note": "rollig_update_evaluation 用于验证段; 不使用冻结全训练段模型"},
        "frozen_note": "仅供参考, 不覆盖冻结产物; code_commit 为本地工作副本",
    }
    (handoff / "replay_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    (metrics_dir / "forecast_rolling_metrics.json").write_text(
        json.dumps(per, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_report(report_dir / "rolling_forecast_report.md", cfg, r, per, manifest)
    _write_handoff_readme(handoff / "README.md", cfg, per)
    return {"manifest": manifest, "metrics": per, "preds": preds, "audit": audit}


def _git_commit() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _fold_summary(r: dict) -> pd.DataFrame:
    rows = []
    for f in r["folds"]:
        fid = f["fold_id"]
        ms = [r["fold_meta"][f"{fid}:{h}"]["meta" if False else "train_rows"] for h in (15, 30, 60) if f"{fid}:{h}" in r["fold_meta"]]
        rows.append({"fold_id": fid, "S": str(f["S"])})
    return pd.DataFrame(rows)


def _write_report(path: Path, cfg: dict, r: dict, per: dict, manifest: dict) -> None:
    lines = ["# 阶段 A6 滚动回放预测报告", "",
             f"- 折数：{len(r['folds'])}（每 {cfg['refit_interval_days']} 天重训，共 {len(r['folds'])*3} 次模型训练）",
             f"- 样本频率：源 {cfg['source_sampling_frequency_minutes']} 分钟，预测发出 {cfg['issue_frequency_minutes']} 分钟",
             f"- 时域：{cfg['horizons_minutes']} 分钟；目标：{cfg['target']['type']}（{cfg['target']['unit']}）",
             f"- 验证段标记：rolling_update_evaluation", "",
             "## 1. 指标（模型 vs lag_1 基线）", "",
             "| 分段/时域 | 模型 MAE | 模型 RMSE | 模型 MAPE | 区间覆盖 | 基线 MAPE |",
             "|-----------|----------|-----------|-----------|----------|-----------|"]
    for k, v in per.items():
        m, b = v["model"], v["baseline_lag_1"]
        lines.append(f"| {k} | {m['MAE']} | {m['RMSE']} | {m['MAPE']} | {m['coverage']} | {b['MAPE']} |")
    lines += ["", "> 说明：本表为滚动回放算法开发/评估材料，非独立最终测试；"
                  "真实历史回放不使用冻结全训练段模型；模型与 lag_1 指标均如实展示。", "",
              "## 2. 防泄漏要点", "",
              "- 每折训练目标 < 校准开始；校准目标 < 预测开始；输入截止 <= as_of；",
              "- 1 分钟时间轴构建因果特征后再抽 5 分钟行（lag_1 仍为 1 分钟前）；",
              "- 环境温湿度仅因果前向填充；缺失率>阈值整折回退 core_no_environment。", "",
              "## 3. 输出", "",
              "- `artifacts/handoff_b/forecast_dev_dispatch.parquet`（六列）",
              "- `artifacts/handoff_b/forecast_validation_dispatch.parquet`（六列）",
              "- `artifacts/handoff_b/forecast_scoring_actuals.parquet`（含 actual_available）",
              "- `artifacts/metrics/forecast_rolling_audit.parquet` + `forecast_fold_metadata.json`",
              "", "![curve]" if False else ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_handoff_readme(path: Path, cfg: dict, per: dict) -> None:
    txt = f"""# B 侧历史回放数据包 (A6)

## 文件
- `forecast_dev_dispatch.parquet` / `forecast_validation_dispatch.parquet`
  六列：as_of, target_time, horizon_minutes, predicted_flow, lower_bound, upper_bound
  主键：(as_of, horizon_minutes)；时域 15/30/60；单位 m3/min；target_time = as_of + horizon（瞬时点）
- `forecast_scoring_actuals.parquet`
  列：split, as_of, target_time, horizon_minutes, actual_flow, actual_available
  join 不到真实流量时 actual_flow=NaN 且 actual_available=False（不 drop）
- `replay_manifest.json` 清单与哈希

## 使用
- 调度时刻 T 读取 as_of=T 的三条记录；禁止把 actual_flow 作输入。
- 事后评分按 (as_of, horizon_minutes) 关联 scoring 表，用 actual_available 过滤。
- 验证段为 rolling_update_evaluation，非静态 holdout。

## 时区/频率
timezone={cfg['timezone']}；timestamp_storage={cfg['timestamp_storage']}；
源 1 分钟；发出 5 分钟。
"""
    path.write_text(txt, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="历史回放导出")
    ap.add_argument("--config", default="configs/rolling_forecast.yaml")
    args = ap.parse_args()
    out = export(args.config)
    print(f"[OK] dev={out['manifest']['outputs']['forecast_dev_dispatch.parquet']['rows']} "
          f"valid={out['manifest']['outputs']['forecast_validation_dispatch.parquet']['rows']} "
          f"scoring={out['manifest']['outputs']['forecast_scoring_actuals.parquet']['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
