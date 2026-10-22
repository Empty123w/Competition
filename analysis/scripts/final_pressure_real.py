"""B 组最终指标: 基于真实历史工况的压力评价。

- 真实基线: 真实 SCADA 母管压力在 CAL-05 带 [0.55,0.66] 与数据分位带下的合格率 (真实观测)。
- 模型校验: 工程一阶动态 (泄漏项) 对真实一步 Δp 的拟合 (MAE / 相关性)。
- 反事实: 用真实观测流量作为需求驱动闭环, 比较 b1 与 joint 的压力合格率/波动 (工程仿真)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from air_station.simulation import closed_loop as CL  # noqa: E402
from air_station.stage2 import joint_dispatch as JD  # noqa: E402

WINDOWS = {"dev": ("2026-03-12", "2026-07-31"), "valid": ("2026-08-01", "2026-08-20"),
           "test": ("2026-08-21", "2026-09-12")}


def _band_stats(p, lo, hi):
    p = np.asarray(p, dtype=float)
    v = p[np.isfinite(p)]
    return {"compliance": round(float(((v >= lo) & (v <= hi)).mean()), 4),
            "mean": round(float(v.mean()), 4), "std": round(float(v.std()), 5),
            "p05": round(float(np.percentile(v, 5)), 4), "p95": round(float(np.percentile(v, 95)), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="valid")
    ap.add_argument("--max-steps", type=int, default=5760)
    a = ap.parse_args()
    out = ROOT / "analysis/reports/b/final"; out.mkdir(parents=True, exist_ok=True)

    st = pd.read_parquet(ROOT / "data/processed/station_timeseries.parquet")
    st["time"] = pd.to_datetime(st["time"])
    t0, t1 = WINDOWS[a.window]
    g = st[(st.time >= t0) & (st.time <= t1)].sort_values("time").dropna(subset=["header_pressure_mpa"])
    g = g.set_index("time")
    dem5 = g["total_flow_m3_min"].resample("5min").mean().dropna().to_numpy()
    p_real = g["header_pressure_mpa"].resample("5min").mean().dropna().to_numpy()
    n = min(len(dem5), len(p_real), a.max_steps)
    dem5, p_real = dem5[:n], p_real[:n]
    p_init = float(p_real[0])
    lo, hi = CL.SimParams().band_lo_mpa, CL.SimParams().band_hi_mpa
    d_lo, d_hi = np.percentile(p_real, 5), np.percentile(p_real, 95)

    # 模型校验: 一阶泄漏动态对真实一步 Δp
    dp_obs = np.diff(p_real)
    dp_hat = -CL.SimParams().leak_per_min * np.maximum(p_real[:-1] - CL.SimParams().p_env_mpa, 0.0) * 5.0
    m = np.isfinite(dp_obs)
    validation = {"n": int(m.sum()),
                  "MAE_mpa": round(float(np.mean(np.abs(dp_obs[m] - dp_hat[m]))), 5),
                  "std_dp_obs": round(float(np.std(dp_obs[m])), 5),
                  "corr": round(float(np.corrcoef(dp_obs[m], dp_hat[m])[0, 1]), 3) if m.sum() > 2 else None,
                  "note": "缺独立需求, 动态压力项不可辨识; 仅校验量级"}

    sc = CL.Scenario(a.window, n_steps=n, base_m3min=float(np.mean(dem5)))
    W = JD.JointWeights(w_energy=1.0, w_low=1.5, w_imbalance=40.0, w_pressure=5000.0,
                        w_center=20000.0, w_switch=2.0, w_short=800.0,
                        balance_mode="spread", spread_tol=0.10)
    res = {}
    for strat in ("b1", "joint"):
        r = JD.simulate(strat, sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                        demand_override=dem5, p_init=p_init,
                        weights=(W if strat == "joint" else None))
        ms = r["metrics"]
        res[strat] = {"compliance_CAL05": ms["pressure_compliance"], "std": ms["pressure_std"],
                      "mean": round(float(np.mean([x["pressure"] for x in r["records"]])), 4),
                      "energy_kwh": ms["energy_kwh"], "supply_m3": ms["supply_m3"],
                      "demand_satisfaction": ms["demand_satisfaction"],
                      "unserved_deficit_m3": ms["unserved_deficit_m3"],
                      "storage_neutral": ms["storage_neutral"],
                      "p_start": ms["pressure_start"], "p_end": ms["pressure_end"]}
    std_red = ((res["b1"]["std"] - res["joint"]["std"]) / res["b1"]["std"] * 100) if res["b1"]["std"] else None
    res["joint"]["std_reduction_vs_b1_pct"] = round(std_red, 2) if std_red is not None else None

    report = {"window": a.window, "steps_5min": n, "p_init": round(p_init, 4),
              "band_CAL05": [lo, hi], "band_data_p5_p95": [round(float(d_lo), 4), round(float(d_hi), 4)],
              "real_baseline": {"CAL05": _band_stats(p_real, lo, hi),
                                "data_band": _band_stats(p_real, d_lo, d_hi)},
              "model_validation": validation, "counterfactual": res}
    (out / f"pressure_real_{a.window}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
