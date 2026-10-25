"""模块贡献: MPC 与 ML 压力模型增量收益 (真实历史工况驱动闭环, 不新增合成场景)。

对照 (同一需求/初始压力/容量/储气/合格带):
  1) reactive_pi  原反应式压力控制
  2) mpc          简单(解析)压力模型 + MPC
  3) mpc_ml       ML(LightGBM)压力模型 + MPC
  4) joint        完整 joint (MPC 多目标; joint_dispatch)
  5) b1           调度基线 (joint_dispatch)
输出: analysis/reports/b/final/contribution_pressure.json + CONTRIBUTION_PRESSURE.md
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from air_station.simulation import closed_loop as CL  # noqa: E402
from air_station.stage2 import joint_dispatch as JD  # noqa: E402
from air_station.stage2 import pressure_model as PM  # noqa: E402
from air_station.stage2 import pressure_sim as PS  # noqa: E402

FIN = ROOT / "analysis/reports/b/final"
CAP = 537.8
WINDOWS = {"valid": ("2026-08-01", "2026-08-20"), "test": ("2026-08-21", "2026-09-12"),
           "dev": ("2026-03-12", "2026-07-31")}


def _series(window, max_steps=None):
    st = pd.read_parquet(ROOT / "data/processed/station_timeseries.parquet")
    st["time"] = pd.to_datetime(st["time"])
    t0, t1 = WINDOWS[window]
    g = st[(st.time >= t0) & (st.time <= t1)].sort_values("time").dropna(subset=["header_pressure_mpa"]).set_index("time")
    dem = g["total_flow_m3_min"].resample("5min").mean().dropna().to_numpy()
    p = g["header_pressure_mpa"].resample("5min").mean().dropna().to_numpy()
    n = min(len(dem), len(p))
    if max_steps:
        n = min(n, max_steps)
    return dem[:n], p[:n]


def _summ(records, sim, cap_step_share=True):
    df = pd.DataFrame(records)
    P = df["pressure"].to_numpy(float); D = df["demand"].to_numpy(float); S = df["supply"].to_numpy(float)
    lo, hi = sim.band_lo_mpa, sim.band_hi_mpa
    dt_h = sim.dt_min / 60.0
    return {"compliance": round(float(((P >= lo) & (P <= hi)).mean()), 4),
            "std": round(float(P.std()), 5),
            "min": round(float(P.min()), 4), "max": round(float(P.max()), 4),
            "energy_kwh": round(float(df.power_kw.fillna(0).sum()) * dt_h, 1),
            "supply_m3": round(float(S.sum()) * sim.dt_min, 1),
            "demand_satisfaction": round(1 - float(np.clip(D - S, 0, None).sum()) / float(D.sum()), 4),
            "unmet_flow_m3": round(float(np.clip(D - S, 0, None).sum()) * sim.dt_min, 1),
            "cap_step_share": round(float((S >= CAP - 1e-6).mean()), 4) if cap_step_share else None}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-steps", type=int, default=6000)
    a = ap.parse_args()
    pm_ml = PM.train("lightgbm")
    rep = {"meta": {"validation_mode": "simulation", "capacity": CAP, "band": [0.55, 0.66],
                    "note": "demand = 真实 SCADA 总供气量(历史代理); 非独立实测用户侧需求"},
           "pressure_model_metrics": pm_ml.metrics, "windows": {}}
    for w in ("valid", "test", "dev"):
        dem, p = _series(w, a.max_steps)
        sc = CL.Scenario(w, n_steps=len(dem), base_m3min=float(np.mean(dem)))
        p_init = float(p[0])
        out = {"steps": len(dem), "p_init": round(p_init, 4)}
        for name, fn in (
            ("reactive_pi", lambda: PS.simulate("reactive_pi", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                                demand_override=dem, p_init=p_init)),
            ("mpc_simple", lambda: PS.simulate("mpc", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                               demand_override=dem, p_init=p_init, pmodel=None)),
            ("mpc_ml", lambda: PS.simulate("mpc", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                           demand_override=dem, p_init=p_init, pmodel=pm_ml)),
            ("b1", lambda: JD.simulate("b1", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                       demand_override=dem, p_init=p_init)),
            ("joint_nompc", lambda: JD.simulate("joint", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                                demand_override=dem, p_init=p_init, weights=JD.JointWeights(
                                                    w_energy=1.0, w_low=1.5, w_imbalance=40.0, w_pressure=0.0,
                                                    w_center=0.0, w_switch=2.0, w_short=800.0,
                                                    balance_mode="spread", spread_tol=0.10, horizon=1))),
            ("joint", lambda: JD.simulate("joint", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                                          demand_override=dem, p_init=p_init, weights=JD.JointWeights(
                                              w_energy=1.0, w_low=1.5, w_imbalance=40.0, w_pressure=5000.0,
                                              w_center=20000.0, w_switch=2.0, w_short=800.0,
                                              balance_mode="spread", spread_tol=0.10))),
        ):
            t0 = time.perf_counter()
            r = fn()
            dt = time.perf_counter() - t0
            m = _summ(r["records"], CL.SimParams(dt_min=5.0))
            m["decide_ms_per_step"] = round(dt / max(1, len(r["records"])) * 1000, 2)
            out[name] = m
        rep["windows"][w] = out
    (FIN / "contribution_pressure.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep["windows"], ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
