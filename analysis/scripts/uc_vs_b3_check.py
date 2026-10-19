"""任务一: 核实 UC(upper) 与 B3 在固定 480 步上是否逐步一致 (只做针对性检查)。

输出: analysis/reports/b/uc_vs_b3_check.json
"""
from __future__ import annotations

import importlib.util
import json
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
OUT = ROOT / "analysis/reports/b/uc_vs_b3_check.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
STEP, N, BLOCK_START = 15, 480, 2400


def main():
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

    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in ("b3", "uc")}
    stt = {s: C.PolicyState() for s in ("b3", "uc")}
    cache = {}
    same_combo = same_flow = both_exec = 0
    b3_fallback = uc_fallback = 0
    diff_feasible_alt = 0
    rows = []
    for _, sr in bd.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        out = {}
        for s in ("b3", "uc"):
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            if s == "b3":
                fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                if rr.status != "success":
                    b3_fallback += 1; out[s] = None; continue
                combo = tuple(a.device_id for a in rr.device_actions if a.running_after)
                flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                acts = rr.device_actions; trans = rr.transition_plan or []
            else:
                req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                             model_path=model, power_cap=1600.0, cfg=UC.UCConfig(demand_basis="upper"), cache=cache)
                if bb is None:
                    uc_fallback += 1; out[s] = None; continue
                combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
            _, blocked, _ = MS._surge(snap, acts, trans)
            ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
            out[s] = {"combo": combo, "flows": {d: round(flows.get(d, 0.0), 3) for d in ("4#", "5#")}, "ok": ok}
            if ok:
                MS._apply(sim[s], acts, ts, stt[s])
        rows.append({"t": ts, "b3": out.get("b3"), "uc": out.get("uc")})
        if out.get("b3") and out.get("uc"):
            both_exec += 1
            if set(out["b3"]["combo"]) == set(out["uc"]["combo"]):
                same_combo += 1
                if all(abs(out["b3"]["flows"][d] - out["uc"]["flows"][d]) <= 1.0 for d in ("4#", "5#")):
                    same_flow += 1
    # 不同可行方案数 (以 UC 在该步是否存在多个不同可行组合判断)
    rep = {"block": {"start": str(bd["t"].iloc[0]), "n": int(len(bd))},
           "both_exec": both_exec, "same_combo": same_combo, "same_flow_within_1m3min": same_flow,
           "b3_fallback_steps": b3_fallback, "uc_fallback_steps": uc_fallback,
           "combo_identical_pct": round(same_combo / both_exec * 100, 2) if both_exec else None,
           "flow_identical_pct": round(same_flow / both_exec * 100, 2) if both_exec else None,
           "ramp": {"unit": "m3/min per 15-min step (running machine)",
                    "source": "data-derived p99(|Δflow per minute|) x STEP",
                    "RAMP_STEP": UC.RAMP_STEP}}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
