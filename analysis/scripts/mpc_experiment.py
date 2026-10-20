"""Pressure-Aware MPC 开发段实验 (同一 480 步块; 含压力与储气分析)。

对比: B1 / B3+B4 / UC(upper) / cand / MPC。
输出: analysis/reports/b/mpc_experiment.json
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization import pressure_mpc as MPC
from air_station.optimization import unit_commitment as UC
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/mpc_experiment.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
_ps = importlib.util.spec_from_file_location("psim2", ROOT / "analysis/scripts/pressure_sim_dynamics_v2.py")
PS = importlib.util.module_from_spec(_ps); _ps.loader.exec_module(PS)

STEP, RATED, N, BLOCK_START = 15, 268.9, 480, 2400
STRAT = ("b1_rule", "b3_b4", "uc_upper", "cand", "mpc")


def main():
    t0 = time.perf_counter()
    base = B1.load_config(B1.DEFAULT_CONFIG); con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    mcfg = MPC.load_mpc_config()
    dev, st = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    bd = bd.iloc[BLOCK_START:BLOCK_START + N].reset_index(drop=True)

    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in STRAT}
    stt = {s: C.PolicyState() for s in STRAT}
    cache = {}
    p_state = {s: 0.60 for s in STRAT}   # MPC/内部压力状态 (供气=计划, 耗气=pred)
    rows = []; rt = {s: [] for s in STRAT}; fallback = {s: 0 for s in STRAT}

    for _, sr in bd.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        r = {"t": ts, "obs": sr["obs"], "pred": sr["pf15"]}
        for s in STRAT:
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0; r[f"{s}_p_plan"] = None
            tt = time.perf_counter()
            try:
                if s == "b1_rule":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        fallback[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    p = 0.0
                    for d, q in flows.items():
                        e = PERF.estimate_power(d, q, model_path=model); p = p + e["estimated_power_kw"] if e["status"] == "success" else None
                        if p is None: break
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif s == "b3_b4":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        fallback[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; p = rr.estimated_power_kw; trans = rr.transition_plan or []
                elif s in ("uc_upper", "cand"):
                    req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    if s == "uc_upper":
                        bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                     model_path=model, power_cap=1600.0, cfg=UC.UCConfig(demand_basis="upper"), cache=cache)
                    else:
                        bb = MS2.plan_v2(req, caps, con, _est(model, cache), cur_on, stt[s].last_switch_at, ts,
                                         1600.0, 2.0, MS.SWITCH_PENALTY_KWH)
                    if bb is None:
                        fallback[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0] if s == "uc_upper" else bb["seq"][0]
                    al = bb["alloc0"]; flows = al["flows"]; p = al["power_kw"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                else:  # mpc
                    pred = [sr["pf15"], sr["pf30"], sr["pf45"], sr["pf60"]]
                    ub = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    bb = MPC.plan_mpc(pred, ub, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                      p_state[s], model_path=model, power_cap=1600.0, cfg=mcfg, cache=cache)
                    if bb is None:
                        fallback[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo, lvl = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]; p = al["power_kw"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, ub[0], con.reserve)[1]
                    r[f"{s}_p_plan"] = bb["p_end"]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = (float(p) if p is not None else None)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
                # 内部压力状态推进 (供气=计划, 耗气=pred) —— MPC 反馈用
                if r[f"{s}_flow"] is not None:
                    p_state[s] = MPC.pressure_step(p_state[s], r[f"{s}_flow"], sr["pf15"], mcfg)
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"; fallback[s] += 1
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
    df = pd.DataFrame(rows)
    dt_h = STEP / 60.0
    ok = df[[f"{s}_status" for s in STRAT]].eq("success").all(axis=1)
    for s in STRAT:
        ok = ok & df[f"{s}_exec"]
    fair = df[ok]
    rep = {"block": {"start": str(bd["t"].iloc[0]), "n": int(len(bd)), "fair_steps": int(len(fair))},
           "per_strategy": {}, "runtime_ms": {}, "fallback_steps": {s: int(fallback[s]) for s in STRAT}}
    for s in STRAT:
        g = df[(df[f"{s}_status"] == "success") & df[f"{s}_exec"]]
        E = float(g[f"{s}_power"].sum()) * dt_h; V = float(g[f"{s}_flow"].sum()) * STEP
        # 压力评测 (独立仿真; 供气=策略流量, 耗气=实测 obs)
        dem = g["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
        sup = g[f"{s}_flow"].fillna(g["obs"]).fillna(0).to_numpy()
        noise = PS._noise(len(dem), PS.NOISE, PS.SEED_BASE + 1)
        pp, _ = PS._simulate(sup, dem, PS.K_VOL, PS.LEAK_PER_MIN, noise, closed_loop=True)
        rep["per_strategy"][s] = {
            "exec_steps": int(len(g)), "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
            "shortfall_vs_actual_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"]).clip(lower=0).sum()) * STEP, 2),
            "sec": round(E / V, 6) if V else None,
            "big_small_min": round(float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                                          + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1),
            "pressure_eval": {"compliance": round(float(((pp >= 0.55) & (pp <= 0.66)).mean()), 4),
                              "below_band_min": int((pp < 0.55).sum()) * STEP,
                              "above_band_min": int((pp > 0.66).sum()) * STEP,
                              "p_start": round(float(pp[0]), 4), "p_end": round(float(pp[-1]), 4)}}
    if len(fair):
        E = {s: float(fair[f"{s}_power"].sum()) * dt_h for s in STRAT}
        rep["fair_energy_kwh"] = {s: round(E[s], 2) for s in STRAT}
        b1 = E["b1_rule"]
        rep["fair_saving_vs_b1_pct"] = {s: (round((b1 - E[s]) / b1 * 100, 4) if b1 else None) for s in STRAT}
        bs = {s: round(float((((fair[f"{s}_f4"] > 0) & (fair[f"{s}_f4"] / RATED < 0.5)).sum()
                              + ((fair[f"{s}_f5"] > 0) & (fair[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1) for s in STRAT}
        rep["fair_big_small_min"] = bs
        rep["fair_big_small_reduction_vs_b1_pct"] = {s: (round((bs["b1_rule"] - bs[s]) / bs["b1_rule"] * 100, 4)
                                                          if bs["b1_rule"] > 0 else "n/a") for s in STRAT}
        rep["fair_shortfall_m3"] = {s: round(float((fair["obs"].fillna(0) - fair[f"{s}_flow"]).clip(lower=0).sum()) * STEP, 2) for s in STRAT}
    rep["runtime_ms"] = {s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
                             "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
                             "max": round(float(np.max(rt[s])) * 1000, 2)} for s in STRAT}
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def _est(model, cache):
    def f(d, q):
        k = (d, round(float(q), 1))
        if k not in cache:
            e = PERF.estimate_power(d, float(q), model_path=model)
            cache[k] = e["estimated_power_kw"] if e["status"] == "success" else None
        return cache[k]
    return f


if __name__ == "__main__":
    main()
