"""阶段: 新机组组合优化器(UC) 开发段快速对比实验 (固定 480 连续步)。

对比: B1 / 冻结 B3+B4 / DP v2(w_low=0) / cand(v2,w_low=2) / UC(upper) / UC 鲁棒(margin 变体)。
另含: 离线理想低负荷下界(诊断, 未来信息, 不参与排名/部署)。
输出: analysis/reports/b/uc_experiment.json
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
from air_station.optimization import unit_commitment as UC
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/uc_experiment.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
_ps = importlib.util.spec_from_file_location("psim2", ROOT / "analysis/scripts/pressure_sim_dynamics_v2.py")
PS = importlib.util.module_from_spec(_ps); _ps.loader.exec_module(PS)

STEP, RATED = 15, 268.9
N = 480
BLOCK_START = 2400   # 固定 dev 连续块起点 (开发段, 非验证段)


def _mk_cfg(basis, margin=0.0, w_low=0.0):
    return UC.UCConfig(w_low=w_low, demand_basis=basis, margin_m3min=margin)


def run(strategies, base_df, device_df, station_df, con, base, model):
    cache = {}
    t0 = pd.Timestamp(base_df["t"].iloc[0])
    init = PIPE.snapshot_from_frames(device_df, station_df, t0)
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in strategies}
    stt = {s: C.PolicyState() for s in strategies}
    rows = []; rt = {s: [] for s in strategies}
    for _, sr in base_df.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        r = {"t": ts, "obs": sr["obs"], "ub15": sr["ub15"]}
        for s, spec in strategies.items():
            kind = spec["kind"]
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            tt = time.perf_counter()
            try:
                if kind == "b1":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    p = 0.0
                    for d, q in flows.items():
                        e = PERF.estimate_power(d, q, model_path=model)
                        p = p + e["estimated_power_kw"] if e["status"] == "success" else None
                        if p is None:
                            break
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif kind == "b3":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; p = rr.estimated_power_kw; trans = rr.transition_plan or []
                elif kind in ("v2", "cand"):
                    req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    b = MS2.plan_v2(req, caps, con, _est(model, cache), cur_on, stt[s].last_switch_at, ts, 1600.0,
                                    (2.0 if kind == "cand" else 0.0), MS.SWITCH_PENALTY_KWH)
                    if b is None:
                        rt[s].append(time.perf_counter() - tt); continue
                    combo = b["seq"][0]; al = b["alloc0"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    flows = al["flows"]; p = al["power_kw"]
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                elif kind == "uc":
                    req = [UC.demand_for(sr["pf15"], sr["ub15"], spec["cfg"]),
                           UC.demand_for(sr["pf30"], sr["ub30"], spec["cfg"]),
                           UC.demand_for(sr["pf45"], sr["ub45"], spec["cfg"]),
                           UC.demand_for(sr["pf60"], sr["ub60"], spec["cfg"])]
                    b = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                model_path=model, power_cap=1600.0, cfg=spec["cfg"], cache=cache)
                    if b is None:
                        rt[s].append(time.perf_counter() - tt); continue
                    combo = b["seq"][0]; al = b["alloc0"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    flows = al["flows"]; p = al["power_kw"]
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = (float(p) if p is not None else None)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
    return pd.DataFrame(rows), rt


def _est(model, cache):
    def f(d, q):
        k = (d, round(float(q), 1))
        if k not in cache:
            e = PERF.estimate_power(d, float(q), model_path=model)
            cache[k] = e["estimated_power_kw"] if e["status"] == "success" else None
        return cache[k]
    return f


def metrics(df, strategies, dt_h=STEP / 60.0):
    out = {}
    # 公平步: 所有策略 success+exec
    ok = df[[f"{s}_status" for s in strategies]].eq("success").all(axis=1)
    for s in strategies:
        ok = ok & df[f"{s}_exec"]
    fair = df[ok]
    out["fair_steps"] = int(len(fair))
    for s in strategies:
        g = df[(df[f"{s}_status"] == "success") & df[f"{s}_exec"]]
        E = float(g[f"{s}_power"].sum()) * dt_h
        V = float(g[f"{s}_flow"].sum()) * STEP
        out[s] = {"exec_steps": int(len(g)), "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "shortfall_vs_actual_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"]).clip(lower=0).sum()) * STEP, 2),
                  "sec": round(E / V, 6) if V else None,
                  "big_small_min": round(float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                                                + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1)}
    if len(fair):
        E = {s: float(fair[f"{s}_power"].sum()) * dt_h for s in strategies}
        out["fair_energy_kwh"] = {s: round(E[s], 2) for s in strategies}
        b1 = E["b1_rule"]
        out["fair_saving_vs_b1_pct"] = {s: (round((b1 - E[s]) / b1 * 100, 4) if b1 else None) for s in strategies}
        bs = {s: out[s]["big_small_min"] for s in strategies}
        # fair big-small (recompute on fair)
        bs = {s: round(float((((fair[f"{s}_f4"] > 0) & (fair[f"{s}_f4"] / RATED < 0.5)).sum()
                              + ((fair[f"{s}_f5"] > 0) & (fair[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1) for s in strategies}
        out["fair_big_small_min"] = bs
        out["fair_big_small_reduction_vs_b1_pct"] = {s: (round((bs["b1_rule"] - bs[s]) / bs["b1_rule"] * 100, 4)
                                                          if bs["b1_rule"] > 0 else "n/a") for s in strategies}
    # 压力 (闭环仿真, 耗时较重的仅主流策略)
    return out, fair


def ideal_lowload(base_df, con, model):
    """离线理想 (未来信息, 仅诊断): 用实测需求求低负荷可行性下界。

    - low_free: 允许过供(抬到非低负荷阈值 134.45)时的最小低负荷步数;
    - low_noover: 不允许过供(供气<=实测需求)时的低负荷步数;
    - energy_free: 达成 low_free 所需能耗 (过供代价)。
    """
    cache = {}; NLT = 0.5 * RATED
    free_steps = 0; noover_steps = 0; energy_free = 0.0; infeas = 0; n = 0
    for _, sr in base_df.iterrows():
        obs = sr["obs"]
        if not np.isfinite(obs) or obs <= 0:
            continue
        n += 1
        if obs * 1.05 > 2 * RATED:      # 容量不可行
            infeas += 1
            continue
        # free: 单机抬到 max(obs,134.45), 或双机 obs/2
        if max(obs, NLT) <= RATED:
            f = max(obs, NLT)
            e = PERF.estimate_power("4#", f, model_path=model)
            if e["status"] == "success":
                energy_free += e["estimated_power_kw"] * STEP / 60.0
            free_steps += 0
        else:
            f = obs / 2.0
            e = PERF.estimate_power("4#", f, model_path=model)
            e2 = PERF.estimate_power("5#", f, model_path=model)
            if e["status"] == "success" and e2["status"] == "success":
                energy_free += (e["estimated_power_kw"] + e2["estimated_power_kw"]) * STEP / 60.0
            free_steps += 0 if f >= NLT else 1
        # no-over: 不允许过供 -> 单机在 obs (若 obs<134.45 即低负荷)
        noover_steps += 1 if obs < NLT else 0
    return {"steps": int(n), "low_min_free_over_supply": round(free_steps * STEP, 1),
            "low_min_no_over_supply": round(noover_steps * STEP, 1),
            "energy_kwh_min_low": round(energy_free, 2), "capacity_infeasible_steps": int(infeas),
            "note": "未来信息诊断, 非可部署; 给出低负荷可行性与过供代价下界"}


def main():
    t0 = time.perf_counter()
    base = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    device_df, station_df = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid})
    bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    bd = bd.iloc[BLOCK_START:BLOCK_START + N].reset_index(drop=True)

    strategies = {
        "b1_rule": {"kind": "b1"},
        "b3_b4": {"kind": "b3"},
        "dp_v2": {"kind": "v2"},
        "cand": {"kind": "cand"},
        "uc_upper": {"kind": "uc", "cfg": _mk_cfg("upper")},
        "uc_mid": {"kind": "uc", "cfg": _mk_cfg("mid")},
        "uc_pred": {"kind": "uc", "cfg": _mk_cfg("pred")},
        "uc_pred20": {"kind": "uc", "cfg": _mk_cfg("pred_plus", margin=20.0)},
        "uc_upper_low2": {"kind": "uc", "cfg": _mk_cfg("upper", w_low=2.0)},
        "uc_upper_low6": {"kind": "uc", "cfg": _mk_cfg("upper", w_low=6.0)},
    }
    df, rt = run(strategies, bd, device_df, station_df, con, base, model)
    m, fair = metrics(df, strategies)
    rep = {"block": {"start": str(bd["t"].iloc[0]), "n": int(len(bd)), "window": "dev calibrated"},
           "metrics": m,
           "runtime_ms": {s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
                              "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
                              "max": round(float(np.max(rt[s])) * 1000, 2)} for s in strategies},
           "ideal_lowload_diagnostic": ideal_lowload(bd, con, model),
           "runtime_s": round(time.perf_counter() - t0, 1)}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("metrics", "runtime_ms", "ideal_lowload_diagnostic", "runtime_s")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
