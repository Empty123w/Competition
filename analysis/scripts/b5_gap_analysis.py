"""B5 前置分析: 赛题 6% 综合能效目标差距核查 (只读分析, 不改算法)。

- 多日期回测 (冻结测试窗口 2026-08-21~09-11)
- B1 规则 / B3 优化 / 历史实测 三基准并列
- 分低/中/高需求统计可行率、能耗、改善
- 电量 energy_kwh = Σ power_kw × step_minutes/60
- 仅在 B1/B3 共同可行步做公平能耗比较

用法: python3 analysis/scripts/b5_gap_analysis.py [step_minutes]
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

OUT = Path("analysis/reports/b/b5_gap_analysis.json")
BAND = (0.55, 0.66)


def deep_merge(base, over):
    import copy
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _constraints(base_cfg, reserve_mode="extra"):
    cfg = deep_merge(base_cfg, {"reserve": {"mode": reserve_mode}})
    return C.DispatchConstraints.from_config(cfg)


def _opt_config(overrides):
    cfg = load_optimization_config()
    if not overrides:
        return "configs/optimization.yaml"
    cfg = deep_merge(cfg, overrides)
    p = Path("/tmp/opencode/b5_opt_cfg.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(p)


def b1_power(base, model):
    tot, ok = 0.0, True
    for a in base.device_actions:
        if a.running_after:
            r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0, model_path=model)
            if r["status"] == "success":
                tot += r["estimated_power_kw"]
            else:
                ok = False
    return tot if ok else None


def run_window(device_df, station_df, forecasts, base_cfg, con, opt_cfg_path, model,
               step_minutes=5):
    times = sorted(forecasts)
    sample = times[::step_minutes]
    recs = []
    for t in sample:
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        f = forecasts[t]
        base = B1.decide(snap, f, [], con, base_cfg, C.PolicyState())
        opt = optimize_dispatch(snap, f, [], con, config_path=opt_cfg_path)
        st = station_df[station_df["time"] == t]
        recs.append({
            "t": str(t), "required": f.demand_value(con.demand_basis),
            "b1_status": base.status, "b3_status": opt.status,
            "b1_flow": base.total_flow_m3_min, "b3_flow": opt.total_flow_m3_min,
            "b1_power": b1_power(base, model) if base.status == "success" else None,
            "b3_power": opt.estimated_power_kw if opt.status == "success" else None,
            "b1_n": sum(1 for a in base.device_actions if a.running_after),
            "b3_n": len(opt.target_combination),
            "b3_surge": opt.surge_status, "b3_blocked": bool(opt.safety_blocked),
            "b3_reserve": opt.reserve_m3_min,
            "obs_flow": float(st["total_flow_m3_min"].iloc[0]) if len(st) else None,
            "obs_power": float(st["total_power_kw"].iloc[0]) if len(st) else None,
            "obs_pressure": float(st["header_pressure_mpa"].iloc[0]) if len(st) else None,
        })
    return pd.DataFrame(recs)


def energy(df, col, step_minutes):
    """电量 kWh = Σ power_kw × step_minutes/60。"""
    s = df[col].dropna()
    return float(s.sum()) * step_minutes / 60.0


def volume(df, col, step_minutes):
    """体积 m3 = Σ flow_(m3/min) × step_minutes。"""
    s = df[col].dropna()
    return float(s.sum()) * float(step_minutes)


def summarize(df, step_minutes, tag):
    common = df[(df.b1_status == "success") & (df.b3_status == "success")].copy()
    n = len(df)
    out = {"tag": tag, "n_steps": n, "step_minutes": step_minutes,
           "b1_feasible": int((df.b1_status == "success").sum()),
           "b3_feasible": int((df.b3_status == "success").sum()),
           "common": len(common),
           "b1_only": int(((df.b1_status == "success") & (df.b3_status != "success")).sum()),
           "b3_only": int(((df.b3_status == "success") & (df.b1_status != "success")).sum())}
    if len(common):
        E1 = energy(common, "b1_power", step_minutes)
        E3 = energy(common, "b3_power", step_minutes)
        V1 = volume(common, "b1_flow", step_minutes)
        V3 = volume(common, "b3_flow", step_minutes)
        Vo = volume(common, "obs_flow", step_minutes)
        Eo = energy(common, "obs_power", step_minutes)
        req = volume(common, "required", step_minutes)
        short1 = float((common["required"] - common["b1_flow"]).clip(lower=0).sum()) * step_minutes
        short3 = float((common["required"] - common["b3_flow"]).clip(lower=0).sum()) * step_minutes
        p = common["obs_pressure"].dropna()
        out.update({
            "required_m3": round(req, 1), "b1_supply_m3": round(V1, 1), "b3_supply_m3": round(V3, 1),
            "obs_supply_m3": round(Vo, 1),
            "b1_shortfall_m3": round(short1, 2), "b3_shortfall_m3": round(short3, 2),
            "b1_energy_kwh": round(E1, 1), "b3_energy_kwh": round(E3, 1), "obs_energy_kwh": round(Eo, 1),
            "b1_sec": round(E1 / V1, 5) if V1 else None,
            "b3_sec": round(E3 / V3, 5) if V3 else None,
            "obs_sec": round(Eo / Vo, 5) if Vo else None,
            "energy_reduction_pct": round((E1 - E3) / E1 * 100, 3) if E1 else None,
            "sec_reduction_pct": round((E1 / V1 - E3 / V3) / (E1 / V1) * 100, 3) if V1 and V3 else None,
            "obs_pressure_mean": round(float(p.mean()), 4) if len(p) else None,
            "obs_pressure_std": round(float(p.std(ddof=0)), 4) if len(p) else None,
            "obs_pressure_compliance": round(float(((p >= BAND[0]) & (p <= BAND[1])).mean()), 4) if len(p) else None,
            "b1_single_share": round(float((common["b1_n"] == 1).mean()), 4),
            "b3_single_share": round(float((common["b3_n"] == 1).mean()), 4),
            "b3_blocked_share": round(float(common["b3_blocked"].mean()), 4),
        })
    return out, common


def by_level(common, step_minutes):
    if not len(common):
        return {}
    q = common["required"].quantile([1 / 3, 2 / 3]).tolist()
    def lvl(x):
        return "low" if x <= q[0] else ("mid" if x <= q[1] else "high")
    common = common.assign(level=common["required"].map(lvl))
    out = {}
    for lv, g in common.groupby("level"):
        E1 = energy(g, "b1_power", step_minutes)
        E3 = energy(g, "b3_power", step_minutes)
        out[lv] = {"n": int(len(g)),
                   "required_mean": round(float(g["required"].mean()), 1),
                   "b1_energy_kwh": round(E1, 1), "b3_energy_kwh": round(E3, 1),
                   "reduction_pct": round((E1 - E3) / E1 * 100, 3) if E1 else None,
                   "b1_single_share": round(float((g["b1_n"] == 1).mean()), 3),
                   "b3_single_share": round(float((g["b3_n"] == 1).mean()), 3)}
    return out


def main():
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = _constraints(base_cfg, "extra")
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    device_df, station_df = PIPE.load_processed()
    forecasts = PIPE.load_frozen_forecasts(horizon_minutes=base_cfg["demand"]["horizon_minutes"])

    t0 = time.perf_counter()
    df = run_window(device_df, station_df, forecasts, base_cfg, con,
                    "configs/optimization.yaml", model, step)
    s, common = summarize(df, step, "default_extra5pct_upper")
    s["by_level"] = by_level(common, step)
    s["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
