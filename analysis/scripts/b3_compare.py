"""B3 B1/B3 同工况对比评估脚本 (修正电量量纲)。

- 步长来自配置 dispatch.step_minutes (数据为 1 分钟)
- 电量: energy_kwh = Σ power_kw * (step_minutes/60)
- 仅在 B1 与 B3 **共同可行**的时间步上做公平比较
- 不可行/缺失/回退结果不计入能耗累计, 单独统计

用法:
  python3 analysis/scripts/b3_compare.py [YYYY-MM-DD]
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from air_station.baseline import rules as B1
from air_station.baseline import pipeline as PIPE
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

METRICS_OUT = Path("analysis/reports/b/optimization_metrics.json")


def b1_power(base, power_model_path):
    total, ok = 0.0, True
    for a in base.device_actions:
        if a.running_after:
            r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0,
                                    model_path=power_model_path)
            if r["status"] == "success":
                total += r["estimated_power_kw"]
            else:
                ok = False
    return total if ok else None


def run(day: str = "2026-09-01") -> dict:
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    opt_cfg = load_optimization_config()
    paths = opt_cfg["paths"]
    power_model = paths["power_model"]
    step_minutes = float(opt_cfg["dispatch"].get("step_minutes", 1.0))

    device_df, station_df = PIPE.load_processed()
    forecasts = PIPE.load_frozen_forecasts(horizon_minutes=base_cfg["demand"]["horizon_minutes"])
    times = sorted(t for t in forecasts if str(t)[:10] == day)

    b1_p, b3_p = [], []
    b1_only = b3_only = 0
    over_cap = 0
    elapsed = []
    reason = Counter()
    combos = Counter()
    for t in times:
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        f = forecasts[t]
        base = B1.decide(snap, f, [], con, base_cfg, C.PolicyState())
        t0 = time.perf_counter()
        opt = optimize_dispatch(snap, f, [], con, policy_version=opt_cfg["dispatch"]["policy_version"])
        elapsed.append(time.perf_counter() - t0)
        b1s, b3s = base.status == "success", opt.status == "success"
        p1 = b1_power(base, power_model) if b1s else None
        p3 = opt.estimated_power_kw if b3s else None
        cap = float(opt_cfg["dispatch"].get("power_cap_kw_per_device", 0.0) or 0.0)
        if p3 is not None:
            for a in opt.device_actions:
                if a.running_after and a.estimated_power_kw and cap and a.estimated_power_kw > cap + 1e-6:
                    over_cap += 1
        for rc in opt.reason_codes:
            reason[rc] += 1
        if b1s and b3s:
            b1_p.append(p1)
            b3_p.append(p3)
            combos[tuple(opt.target_combination)] += 1
        elif b1s:
            b1_only += 1
        elif b3s:
            b3_only += 1

    n = len(times)
    common = len(b1_p)
    E1 = OPT.sum_energy_kwh(b1_p, step_minutes)
    E3 = OPT.sum_energy_kwh(b3_p, step_minutes)
    metrics = {
        "validation_mode": "simulation",
        "basis": "model_estimate",
        "day": day,
        "time_range": [str(times[0]), str(times[-1])] if times else [],
        "n_steps": n,
        "step_minutes": step_minutes,
        "power_unit": "kW",
        "energy_unit": "kWh",
        "common_feasible_steps": common,
        "b1_only_feasible": b1_only,
        "b3_only_feasible": b3_only,
        "b1_feasible": common + b1_only,
        "b3_feasible": common + b3_only,
        "horizon_hours_common": round(common * step_minutes / 60.0, 3),
        "b1_energy_kwh": round(E1, 2),
        "b3_energy_kwh": round(E3, 2),
        "b1_avg_power_kw": round(E1 / (common * step_minutes / 60.0), 2) if common else None,
        "b3_avg_power_kw": round(E3 / (common * step_minutes / 60.0), 2) if common else None,
        "energy_reduction_pct": round((E1 - E3) / E1 * 100.0, 3) if E1 > 0 else None,
        "switches": {"b1": 0, "b3": 0},
        "power_over_cap_count": over_cap,
        "reason_codes": dict(reason),
        "combo_histogram": {str(k): v for k, v in combos.items()},
        "elapsed_seconds": {"min": round(min(elapsed), 4), "median": round(statistics.median(elapsed), 4),
                            "max": round(max(elapsed), 4)} if elapsed else {},
        "note": "同工况(相同需求/备用/压力/约束/预测)模型估计; 仅为仿真, 非现场实测节能",
    }
    return metrics


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    m = run(day)
    METRICS_OUT.parent.mkdir(parents=True, exist_ok=True)
    METRICS_OUT.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: m[k] for k in ("day", "n_steps", "common_feasible_steps",
                                        "b1_energy_kwh", "b3_energy_kwh", "b1_avg_power_kw",
                                        "b3_avg_power_kw", "energy_reduction_pct",
                                        "power_over_cap_count", "elapsed_seconds")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
