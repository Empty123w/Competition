"""均衡分配候选实验 (固定开发段 480 步块)。

策略: B1 / B3+B4 / cand / bal(均衡: 需求允许时每台>=134.45)。
输出: analysis/reports/b/balanced_experiment.json
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
OUT = ROOT / "analysis/reports/b/balanced_experiment.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
STEP, RATED, NLT, BLOCK_START, N = 15, 268.9, 134.45, 2400, 480
STRAT = ("b1_rule", "b3_b4", "cand", "bal")


def main():
    t0 = time.perf_counter()
    base = B1.load_config(B1.DEFAULT_CONFIG); con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    dev, st = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    bd = bd.iloc[BLOCK_START:BLOCK_START + N].reset_index(drop=True)
    cache = {}
    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in STRAT}
    stt = {s: C.PolicyState() for s in STRAT}
    rows = []
    for _, sr in bd.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        r = {"t": ts, "obs": sr["obs"]}
        for s in STRAT:
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = None; r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            try:
                if s == "b1_rule":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif s == "b3_b4":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                else:
                    req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    cfg = UC.UCConfig(demand_basis="upper", balanced=(s == "bal"),
                                      w_low=(2.0 if s == "cand" else 0.0))
                    bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                 model_path=model, power_cap=1600.0, cfg=cfg, cache=cache)
                    if bb is None:
                        continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = _p(flows, model, cache)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"
        rows.append(r)
    df = pd.DataFrame(rows); dt_h = STEP / 60.0
    ok = df[[f"{s}_status" for s in STRAT]].eq("success").all(axis=1)
    for s in STRAT:
        ok = ok & df[f"{s}_exec"]
    fair = df[ok]
    b1e = float(fair["b1_rule_power"].fillna(0).sum()) * dt_h
    bs1 = float((((fair["b1_rule_f4"] > 0) & (fair["b1_rule_f4"] / RATED < 0.5)).sum()
                 + ((fair["b1_rule_f5"] > 0) & (fair["b1_rule_f5"] / RATED < 0.5)).sum()) * STEP)
    rep = {"block": {"start": str(bd["t"].iloc[0]), "n": int(len(bd)), "fair": int(len(fair))}, "per": {}}
    for s in STRAT:
        E = float(fair[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(fair[f"{s}_flow"].fillna(0).sum()) * STEP
        bs = float((((fair[f"{s}_f4"] > 0) & (fair[f"{s}_f4"] / RATED < 0.5)).sum()
                    + ((fair[f"{s}_f5"] > 0) & (fair[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
        rep["per"][s] = {"energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                         "saving_vs_b1_pct": round((b1e - E) / b1e * 100, 4) if b1e else None,
                         "gap_m3": round(float((fair["obs"].fillna(0) - fair[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
                         "low_min": round(bs, 1),
                         "low_reduction_vs_b1_pct": round((bs1 - bs) / bs1 * 100, 4) if bs1 > 0 else "n/a",
                         "extra_supply_vs_b1_m3": round(V - float(fair["b1_rule_flow"].fillna(0).sum()) * STEP, 1)}
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def _p(flows, model, cache):
    tot = 0.0
    for d, q in flows.items():
        if q and q > 0:
            k = (d, round(float(q), 1))
            if k not in cache:
                e = PERF.estimate_power(d, float(q), model_path=model)
                cache[k] = e["estimated_power_kw"] if e["status"] == "success" else None
            if cache[k] is None:
                return None
            tot += cache[k]
    return tot


if __name__ == "__main__":
    main()
