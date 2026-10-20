"""阶段一: A6 test 滚动校准预测 (B 侧隔离生成, 不覆盖 A 的 dev/valid 交付)。

- 读取隔离配置 configs/rolling_forecast_test.yaml (first_as_of=08-21, valid.end=09-12,
  独立 model_dir/handoff_dir/report_dir)。
- 复用 A6 原方法: rolling_backtest.run (扩展窗口/7天重训/因果特征/5min抽取) +
  calibrate._cal_table (滚动共形残差分位, 仅用 as_of 之前已实现残差)。
- 输出与 dev/valid 同口径: as_of,target_time,horizon_minutes(15/30/45/60/75),
  predicted_flow,lower_bound,upper_bound,pred_mean_interval,pred_upper_interval。
- 另写 scoring actuals (仅供评估) 与 manifest/hash。
不修改 A 代码; 不改 dev/valid 交付。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.models.forecasting import calibrate as CAL
from air_station.models.forecasting import rolling_backtest as RB
from air_station.models.forecasting import rolling_features as RF

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs/rolling_forecast_test.yaml"
TIME = "time"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


def main() -> int:
    t0 = time.perf_counter()
    cfg = RF.load_config(str(CFG))
    handoff = ROOT / cfg["paths"]["handoff_dir"]; handoff.mkdir(parents=True, exist_ok=True)
    cal = cfg["calibration"]
    horizons = cfg["horizons_minutes"]

    print(f"[test-replay] start cfg={CFG.name} horizons={horizons} "
          f"first_as_of={cfg['first_as_of']} valid_end={cfg['valid']['end']}", flush=True)
    r = RB.run(str(CFG))
    preds = r["preds"].copy()
    if preds.empty:
        print("[test-replay] ABORT: 无预测生成", flush=True); return 1
    preds["as_of"] = pd.to_datetime(preds["as_of"]); preds["target_time"] = pd.to_datetime(preds["target_time"])
    print(f"[test-replay] folds={len(r['folds'])} raw_preds={len(preds)} "
          f"as_of {preds.as_of.min()} -> {preds.as_of.max()}", flush=True)

    st = pd.read_parquet(Path(cfg["paths"]["processed_dir"]) / "station_timeseries.parquet")
    st[TIME] = pd.to_datetime(st[TIME]); act = st.set_index(TIME)["total_flow_m3_min"]
    preds["actual_flow"] = preds["target_time"].map(act)

    # 滚动共形校准 (与 A6 完全一致的实现: 复用 CAL._cal_table)
    out = []
    for h in horizons:
        sub = preds[preds["horizon_minutes"] == h].sort_values("as_of").copy()
        ct = CAL._cal_table(sub, int(cal["window_days"]), int(cal["min_samples"]), float(cal["alpha"]))
        sub = pd.merge_asof(sub.sort_values("as_of"), ct.sort_values("cal_time"),
                            left_on="as_of", right_on="cal_time",
                            direction="backward", allow_exact_matches=False)
        orig_hw = (sub["upper_bound"] - sub["predicted_flow"]).clip(lower=0)
        q = sub["q"].fillna(orig_hw)
        sub["lower_bound"] = (sub["predicted_flow"] - q).clip(lower=0.0)
        sub["upper_bound"] = (sub["predicted_flow"] + q).clip(lower=0.0)
        out.append(sub)
    preds = pd.concat(out, ignore_index=True)

    preds = preds.sort_values(["as_of", "horizon_minutes"])
    g = preds.groupby("as_of", sort=False)
    preds["pred_mean_interval"] = g["predicted_flow"].transform(lambda s: s.expanding().mean())
    preds["pred_upper_interval"] = g["upper_bound"].transform(lambda s: s.expanding().max())

    core = ["as_of", "target_time", "horizon_minutes", "predicted_flow", "lower_bound", "upper_bound"]
    ext = core + ["pred_mean_interval", "pred_upper_interval"]
    dispatch = preds[ext].reset_index(drop=True)
    scoring = preds[core + ["actual_flow"]].copy()
    scoring["split"] = "test"
    scoring = scoring[["split", "as_of", "target_time", "horizon_minutes", "actual_flow"]].reset_index(drop=True)

    dp = handoff / "forecast_test_dispatch_calibrated.parquet"
    sp = handoff / "forecast_test_scoring_actuals_calibrated.parquet"
    dispatch.to_parquet(dp, index=False); scoring.to_parquet(sp, index=False)

    # 网格与步数核查
    grid = sorted({t for t in dispatch["as_of"] if t.minute % 15 == 0
                   and set(dispatch[dispatch.as_of == t]["horizon_minutes"]) >= {15, 30, 45, 60}})
    cov = {}
    for h in horizons:
        s = preds[preds.horizon_minutes == h].dropna(subset=["actual_flow"])
        cov[str(h)] = round(float(((s.actual_flow >= s.lower_bound) & (s.actual_flow <= s.upper_bound)).mean()), 4)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "isolation_note": "B 侧隔离生成; 不覆盖 A 的 dev/valid 交付; test 段曾被 B5 早期使用(非盲测)",
        "config": str(CFG.relative_to(ROOT)),
        "horizons_minutes": horizons, "issue_frequency_minutes": cfg["issue_frequency_minutes"],
        "range": {"as_of_min": str(preds.as_of.min()), "as_of_max": str(preds.as_of.max()),
                  "target_max": str(preds.target_time.max())},
        "folds": len(r["folds"]), "interval_method": "rolling conformal (A6 identical)",
        "alpha": float(cal["alpha"]), "nominal_coverage": round(1 - float(cal["alpha"]), 4),
        "coverage_test": cov,
        "grid_steps_all_horizons": int(len(grid)),
        "grid_first_last": [str(grid[0]), str(grid[-1])] if grid else None,
        "outputs": {
            "forecast_test_dispatch_calibrated.parquet": {"sha256": sha256(dp), "rows": int(len(dispatch)),
                                                          "cols": ext},
            "forecast_test_scoring_actuals_calibrated.parquet": {"sha256": sha256(sp), "rows": int(len(scoring))},
        },
        "runtime_s": round(time.perf_counter() - t0, 1),
    }
    (handoff / "test_replay_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    print("[test-replay] done", json.dumps({"grid_steps": len(grid), "coverage": cov,
                                             "rows": len(dispatch)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
