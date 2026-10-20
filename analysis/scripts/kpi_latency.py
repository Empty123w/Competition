"""调度耗时 KPI: B3 与 bal 求解时延 + 超时回退行为验证。

- 复用账本 runtime_ms (全量逐步)。
- 直接调用 dispatch_api 在验证段抽 N 步测量 p50/p95/max。
- 验证 timeout_ms 触发回退。
输出: analysis/reports/b/kpi_latency.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"


def _ms(rt):
    return {"p50": round(float(np.percentile(rt, 50)), 2),
            "p95": round(float(np.percentile(rt, 95)), 2),
            "max": round(float(np.max(rt)), 2)}


def main():
    rep = {"target_s": 30.0, "b_only_target_s": 60.0}
    for w, jf in (("dev", "multikpi_dev_0_13000.json"), ("valid", "multikpi_valid_0_2000.json")):
        d = json.loads((ROOT / "analysis/reports/b" / jf).read_text(encoding="utf-8"))
        rep[f"ledger_{w}"] = {s: d["runtime_ms"][s] for s in ("b3_b4", "bal") if s in d.get("runtime_ms", {})}

    # 直接 API 计时 (验证段抽 200 步)
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    bundle = load_forecast_bundle(HB / "forecast_validation_dispatch_calibrated.parquet",
                                  scoring=scor, scoring_split="valid")
    dev, st = PIPE.load_processed()
    grid = [t for t in sorted(bundle.by_issue) if all(h in bundle.by_issue[t] for h in (15, 30, 45, 60))][:200]
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    times = {"b3": [], "balanced": []}
    fb = {"b3": 0, "balanced": 0}
    for t in grid:
        snap = PIPE.snapshot_from_frames(dev, st, t)
        fr = bundle.forecast_result(t, 15)
        for mode in ("b3", "balanced"):
            t0 = time.perf_counter()
            out = API.plan_dispatch(mode, snap, fr, [], con)
            times[mode].append((time.perf_counter() - t0) * 1000)
            fb[mode] += 1 if out.get("is_fallback") else 0
    rep["api_valid_sample"] = {"n": len(grid),
                               "b3": _ms(times["b3"]), "balanced": _ms(times["balanced"]),
                               "b3_fallback": fb["b3"], "balanced_fallback": fb["balanced"]}

    # 超时回退验证
    t = grid[0]
    snap = PIPE.snapshot_from_frames(dev, st, t)
    fr = bundle.forecast_result(t, 15)
    to = API.plan_dispatch("balanced", snap, fr, [], con, timeout_ms=-1.0)
    rep["timeout_fallback_ok"] = bool(to.get("is_fallback") and to.get("status") != C.STATUS_SUCCESS)
    OUT = ROOT / "analysis/reports/b/kpi_latency.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
