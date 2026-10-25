"""dev 压力合格率 71.45% 根因分析 + valid/test 敏感性核实。

- 输入: 真实 SCADA 站级数据 (5min 重采样) 作为历史代理需求; joint/b1 闭环 (工程压力模型)。
- 输出: analysis/reports/b/final/{pressure_dev_analysis.json,PRESSURE_DEV_ANALYSIS.md,
        pressure_dev_joint_ledger.csv}
分析维度:
  1) 总步/合格/不合格/合格率
  2) 超容量步 (需求 > 537.8) 数量/占比/连续段/超额流量
  3) 不合格 ∩ 超容量
  4) 超容量段结束后的恢复滞后 (储气/控制滞后)
  5) 容量充足但不合格 (稳态控制) 的占比
  6) 物理可行连续工况 (需求<=0.95*容量) 的压力合格率
  7) valid/test 对 p_init / K_VOL 的敏感性
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from air_station.simulation import closed_loop as CL  # noqa: E402
from air_station.stage2 import joint_dispatch as JD  # noqa: E402

FIN = ROOT / "analysis/reports/b/final"
CAP = 537.8
WINDOWS = {"dev": ("2026-03-12", "2026-07-31"), "valid": ("2026-08-01", "2026-08-20"),
           "test": ("2026-08-21", "2026-09-12")}


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


def _run(window, dem, p_init, k_vol=None, max_steps=None):
    sc = CL.Scenario(window, n_steps=len(dem), base_m3min=float(np.mean(dem)))
    sim = CL.SimParams(dt_min=5.0, seed=0)
    if k_vol is not None:
        sim.k_vol_m3_per_mpa = float(k_vol)
    W = JD.JointWeights(w_energy=1.0, w_low=1.5, w_imbalance=40.0, w_pressure=5000.0,
                        w_center=20000.0, w_switch=2.0, w_short=800.0,
                        balance_mode="spread", spread_tol=0.10)
    r = JD.simulate("joint", sc, sim=sim, demand_override=dem, p_init=p_init, weights=W)
    return r


def _runs(mask):
    runs = []
    i = 0
    n = len(mask)
    while i < n:
        if not mask[i]:
            i += 1; continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        runs.append((i, j)); i = j + 1
    return runs


def analyze_dev(max_steps=None):
    dem, p = _series("dev", max_steps)
    p_init = float(p[0])
    r = _run("dev", dem, p_init, max_steps=max_steps)
    rec = pd.DataFrame(r["records"])
    P = rec["pressure"].to_numpy(float); D = rec["demand"].to_numpy(float)
    S = rec["supply"].to_numpy(float)
    lo, hi = CL.SimParams().band_lo_mpa, CL.SimParams().band_hi_mpa
    ok = (P >= lo) & (P <= hi)
    over = D > CAP + 1e-6
    n = len(P)
    over_runs = _runs(over)

    # 恢复滞后: 超容量段结束后到压力回到带内
    recov_noncompl = 0; recov_steps = []
    max_recov = 24  # 2h
    for (a, b) in over_runs:
        t = b + 1; cnt = 0
        while t < n and not ok[t] and (t - b) <= max_recov:
            cnt += 1; t += 1
        recov_noncompl += cnt; recov_steps.append(cnt)

    # 逐步分类不合格原因
    cls = np.array(["ok"] * n, dtype=object)
    for i in range(n):
        if ok[i]:
            continue
        if over[i]:
            cls[i] = "over_capacity"
        else:
            # 是否处于任一超容量段后的恢复窗内
            in_recov = False
            for (a, b) in over_runs:
                if b < i <= b + max_recov and not ok[i]:
                    # 直到恢复或超窗
                    t = b + 1; seen = False
                    while t <= i:
                        if ok[t]:
                            seen = True; break
                        t += 1
                    if not seen:
                        in_recov = True
                    break
            cls[i] = "recovery" if in_recov else "steady_control"
    below = (~ok) & (P < lo); above = (~ok) & (P > hi)

    # 物理可行连续工况: 需求 <= 0.95*CAP 的连续段 (长 >= 12 步=3h)
    feas = D <= 0.95 * CAP
    feas_runs = [(a, b) for (a, b) in _runs(feas) if (b - a + 1) >= 12]
    feas_idx = np.concatenate([np.arange(a, b + 1) for (a, b) in feas_runs]) if feas_runs else np.array([], int)
    feas_comp = float(ok[feas_idx].mean()) if feas_idx.size else None
    capfeas_idx = np.where(D <= CAP)[0]
    capfeas_comp = float(ok[capfeas_idx].mean()) if capfeas_idx.size else None

    out = {
        "window": "dev", "steps": n, "p_init": round(p_init, 4), "band": [lo, hi], "capacity": CAP,
        "compliance": round(float(ok.mean()), 4),
        "compliant_steps": int(ok.sum()), "noncompliant_steps": int((~ok).sum()),
        "noncompliant_below_lo": int(below.sum()), "noncompliant_above_hi": int(above.sum()),
        "over_capacity_steps": int(over.sum()), "over_capacity_share": round(float(over.mean()), 4),
        "over_capacity_runs": len(over_runs),
        "over_capacity_run_len_max": int(max((b - a + 1) for (a, b) in over_runs)) if over_runs else 0,
        "over_capacity_minutes_max": int(max((b - a + 1) for (a, b) in over_runs) * 5) if over_runs else 0,
        "excess_flow_m3": round(float((D[over] - CAP).sum() * 5), 1),
        "necessity_intersection_steps": int((over & (~ok)).sum()),
        "noncompliant_capacity_feasible": int(((~ok) & (~over)).sum()),
        "recovery_noncompliant_steps": int(recov_noncompl),
        "recovery_max_steps": int(max(recov_steps)) if recov_steps else 0,
        "class_counts": {k: int((cls == k).sum()) for k in ("ok", "over_capacity", "recovery", "steady_control")},
        "compliance_capacity_feasible": round(capfeas_comp, 4),
        "compliance_feasible_continuous": round(feas_comp, 4) if feas_comp is not None else None,
        "feasible_continuous_segments": len(feas_runs),
        "feasible_continuous_steps": int(feas_idx.size),
        "supply_capped_steps": int((S >= CAP - 1e-6).sum()),
        "fallback_steps": int(rec.get("fallback", pd.Series([False] * n)).sum()) if "fallback" in rec else None,
    }
    return out, rec


def sensitivity(window, max_steps=2000):
    dem, p = _series(window, max_steps)
    p_init = float(p[0])
    lo, hi = CL.SimParams().band_lo_mpa, CL.SimParams().band_hi_mpa
    out = {"p_init_real": round(p_init, 4), "runs": []}
    for tag, pi, kv in (("baseline", p_init, None),
                        ("p_init_056", 0.56, None), ("p_init_060", 0.60, None), ("p_init_062", 0.62, None),
                        ("kvol_half", p_init, 11850.0), ("kvol_double", p_init, 47400.0)):
        r = _run(window, dem, pi, kv)
        comp = float(((r["metrics"]["pressure_compliance"])))
        out["runs"].append({"tag": tag, "p_init": round(pi, 3),
                            "k_vol": kv if kv else 23700.0,
                            "compliance": comp, "std": r["metrics"]["pressure_std"],
                            "energy_kwh": r["metrics"]["energy_kwh"]})
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-steps", type=int, default=0)
    ap.add_argument("--sens-steps", type=int, default=2000)
    a = ap.parse_args()
    dev_stats, rec = analyze_dev(a.dev_steps or None)
    rec[["k", "demand", "supply", "pressure", "fallback", "status"]].to_csv(
        FIN / "pressure_dev_joint_ledger.csv", index=False)
    sens = {w: sensitivity(w, a.sens_steps) for w in ("valid", "test")}
    rep = {"dev": dev_stats, "sensitivity": sens,
           "note": "demand = 真实 SCADA 站级总供气量 (5min 均值) 作为历史代理需求; 非独立实测用户侧需求"}
    (FIN / "pressure_dev_analysis.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dev_stats, ensure_ascii=False, indent=1))
    for w, s in sens.items():
        print("== sensitivity", w)
        for r in s["runs"]:
            print("   ", r)


if __name__ == "__main__":
    main()
