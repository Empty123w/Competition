"""选择性均衡分配 (SBD) Pareto 实验 — 固定开发段 480 步块。

策略: B1 / B3+B4 / cand / bal / SBD(阈值网格)。
输出: analysis/reports/b/sbd_experiment.json / _ledger.csv
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
from air_station.optimization import selective_balanced as SBD
from air_station.optimization import unit_commitment as UC
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/sbd_experiment.json"
LED = ROOT / "analysis/reports/b/sbd_experiment_ledger.csv"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
STEP, RATED, NLT, BLOCK_START, N, P0 = 15, 268.9, 134.45, 2400, 480, 0.60
K_VOL, LEAK, P_ENV = 23700.0, 0.001, 0.55
STRAT = ("b1_rule", "b3_b4", "cand", "bal", "sbd_t0", "sbd_t3", "sbd_t5", "sbd_t7", "sbd_t10", "sbd_t20")


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
    thr = {"sbd_t0": 0.0, "sbd_t3": 3.0, "sbd_t5": 5.0, "sbd_t7": 7.0, "sbd_t10": 10.0, "sbd_t20": 20.0}
    rows = []; rt = {s: [] for s in STRAT}
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
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0; r[f"{s}_chosen"] = ""
            tt = time.perf_counter()
            try:
                if s == "b1_rule":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif s == "b3_b4":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                else:
                    req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    if s == "cand":
                        bb = MS2.plan_v2(req, caps, con, _est(model, cache), cur_on, stt[s].last_switch_at, ts, 1600.0, 2.0, MS.SWITCH_PENALTY_KWH)
                    elif s == "bal":
                        bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                     model_path=model, power_cap=1600.0, cfg=UC.UCConfig(demand_basis="upper", balanced=True), cache=cache)
                    else:
                        bb = SBD.plan_sbd(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                          SBD.SBDConfig(max_extra_kwh_per_low_step=thr[s]), model_path=model, power_cap=1600.0, cache=cache)
                    if bb is None:
                        rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                    r[f"{s}_chosen"] = bb.get("chosen", "")
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
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
    df = pd.DataFrame(rows); df.to_csv(LED, index=False)
    dt_h = STEP / 60.0
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
        # 压力 (P0, CRN=0)
        dem = fair["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
        sup = fair[f"{s}_flow"].fillna(fair["obs"]).fillna(0).to_numpy()
        p = P0; ps = []
        for i in range(len(dem)):
            p = min(max(p + STEP * ((sup[i] - dem[i]) / K_VOL - LEAK * (p - P_ENV)), 0.40), 0.75); ps.append(p)
        ps = np.array(ps)
        # 逐机加载率 (运行)
        loads = np.concatenate([fair[f"{s}_f{d[0]}"].to_numpy(float)[fair[f"{s}_f{d[0]}"].to_numpy(float) > 0] / RATED for d in ("4#", "5#")])
        rep["per"][s] = {
            "energy_kwh": round(E, 2), "supply_m3": round(V, 1), "sec": round(E / V, 6) if V else None,
            "saving_vs_b1_pct": round((b1e - E) / b1e * 100, 4) if b1e else None,
            "gap_m3": round(float((fair["obs"].fillna(0) - fair[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
            "low_min": round(bs, 1), "low_reduction_vs_b1_pct": round((bs1 - bs) / bs1 * 100, 4) if bs1 > 0 else "n/a",
            "load_mean": round(float(loads.mean()), 4) if len(loads) else None,
            "load_p10": round(float(np.percentile(loads, 10)), 4) if len(loads) else None,
            "load_barely_above_half_frac": round(float(((loads >= 0.5) & (loads < 0.55)).mean()), 4) if len(loads) else None,
            "pressure": {"compliance": round(float(((ps >= 0.55) & (ps <= 0.66)).mean()), 4),
                         "pinned_upper_frac": round(float((ps >= 0.7499).mean()), 4), "p_end": round(float(ps[-1]), 4)},
            "chosen_bal_frac": (round(float((fair[f"{s}_chosen"] == "bal").mean()), 4) if f"{s}_chosen" in fair and s.startswith("sbd") else None)}
    rep["runtime_ms"] = {s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
                             "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
                             "max": round(float(np.max(rt[s])) * 1000, 2)} for s in STRAT}
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
