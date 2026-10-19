"""统一多指标实验入口 (dev/valid; 固定窗口; 全窗口+共同可行+服务等价; Pareto)。

候选参数化: B1 / B3 / cand / bal / UC(demand_k) / SBD。
支持断点: 每 CKPT 步写 _ckpt CSV + progress log。
输出: analysis/reports/b/multikpi_<window>[_<start>_<n>].json + _ledger.csv
"""
from __future__ import annotations

import argparse
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
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
STEP, RATED, NLT = 15, 268.9, 134.45
P0 = 0.60; K_VOL, LEAK, P_ENV = 23700.0, 0.001, 0.55
CKPT = 250

# 候选: name -> spec
CANDS = {
    "b1_rule": {"kind": "b1"}, "b3_b4": {"kind": "b3"}, "cand": {"kind": "cand"},
    "bal": {"kind": "bal"},
    "k0.75": {"kind": "uc", "k": 0.75}, "k0.50": {"kind": "uc", "k": 0.50},
    "k0.25": {"kind": "uc", "k": 0.25}, "k0.00": {"kind": "uc", "k": 0.0},
    "bal_k0.75": {"kind": "bal", "k": 0.75}, "k0.33": {"kind": "uc", "k": 0.33}, "k0.15": {"kind": "uc", "k": 0.15},
    "sbd_t7": {"kind": "sbd", "thr": 7.0}, "sbd_t3": {"kind": "sbd", "thr": 3.0},
    "sbd_t5": {"kind": "sbd", "thr": 5.0}, "sbd_t6": {"kind": "sbd", "thr": 6.0},
    "sbd_t8": {"kind": "sbd", "thr": 8.0}, "sbd_t10": {"kind": "sbd", "thr": 10.0},
    "eab_c05": {"kind": "eab", "cap": 0.5}, "eab_c1": {"kind": "eab", "cap": 1.0},
    "eab_c2": {"kind": "eab", "cap": 2.0}, "eab_c5": {"kind": "eab", "cap": 5.0},
    "eab_lb06_c1": {"kind": "eab", "cap": 1.0, "lb": 0.6},
    "eab_lb06_c3": {"kind": "eab", "cap": 3.0, "lb": 0.6},
}


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


def run(window, start, n, cands, logpath):
    base = B1.load_config(B1.DEFAULT_CONFIG); con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    dev, st = PIPE.load_processed()
    scor = pd.read_parquet(HB / f"forecast_scoring_actuals_calibrated.parquet")
    fn = "forecast_dev_dispatch_calibrated.parquet" if window == "dev" else "forecast_validation_dispatch_calibrated.parquet"
    split = "dev" if window == "dev" else "valid"
    b = load_forecast_bundle(HB / fn, scoring=scor, scoring_split=split)
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    if start:
        bd = bd.iloc[start:start + (n or len(bd))].reset_index(drop=True)
    elif n:
        bd = bd.iloc[:n].reset_index(drop=True)
    strategies = list(cands)
    cache = {}
    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in strategies}
    stt = {s: C.PolicyState() for s in strategies}
    rows = []; rt = {s: [] for s in strategies}; fb = {s: 0 for s in strategies}
    t0 = time.perf_counter()
    log = open(logpath, "a")
    log.write(f"[start] window={window} n={len(bd)} strategies={strategies}\n"); log.flush()
    for i, (_, sr) in enumerate(bd.iterrows()):
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        r = {"t": ts, "obs": sr["obs"]}
        for s in strategies:
            spec = cands[s]; kind = spec["kind"]
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = None; r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            tt = time.perf_counter()
            try:
                if kind == "b1":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif kind == "b3":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif kind == "cand":
                    req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    bb = MS2.plan_v2(req, caps, con, _est(model, cache), cur_on, stt[s].last_switch_at, ts, 1600.0, 2.0, MS.SWITCH_PENALTY_KWH)
                    if bb is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                elif kind in ("uc", "bal", "sbd"):
                    k = spec.get("k", 1.0)
                    req = [UC.demand_scaled(sr[f"pf{h}"], sr[f"ub{h}"], k) for h in (15, 30, 45, 60)]
                    if kind == "sbd":
                        bb = SBD.plan_sbd(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                          SBD.SBDConfig(max_extra_kwh_per_low_step=spec.get("thr", 7.0)),
                                          model_path=model, power_cap=1600.0, cache=cache)
                    else:
                        cfg = UC.UCConfig(demand_basis="upper", balanced=(kind == "bal"))
                        bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                     model_path=model, power_cap=1600.0, cfg=cfg, cache=cache)
                    if bb is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                elif kind == "eab":
                    from air_station.optimization import energy_aware_balanced as EAB
                    req = [UC.demand_scaled(sr[f"pf{h}"], sr[f"ub{h}"], 1.0) for h in (15, 30, 45, 60)]
                    bb = EAB.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                  model_path=model, power_cap=1600.0,
                                  cfg=EAB.EABConfig(lb_frac=spec.get("lb", 0.5),
                                                    cost_cap_kwh_per_step=spec.get("cap", float("inf"))),
                                  cache=cache)
                    if bb is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
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
                r[f"{s}_status"] = f"error:{type(e).__name__}"; fb[s] += 1
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
        if (i + 1) % CKPT == 0 or i == len(bd) - 1:
            pd.DataFrame(rows).to_csv(ROOT / f"analysis/reports/b/multikpi_{window}_ckpt.csv", index=False)
            log.write(f"[ckpt] {i+1}/{len(bd)} steps; best-not-yet; elapsed={time.perf_counter()-t0:.0f}s\n"); log.flush()
    log.close()
    return pd.DataFrame(rows), rt, fb


def kpis(df, strategies):
    dt_h = STEP / 60.0
    out = {"n_total": int(len(df)), "per": {}, "scopes": {}}
    b1 = df["b1_rule_exec"].fillna(False) & df["b1_rule_status"].eq("success")
    scopes = {"full": df.index, }
    common = df[[f"{s}_status" for s in strategies]].eq("success").all(axis=1)
    for s in strategies:
        common = common & df[f"{s}_exec"]
    scopes["common"] = df.index[common]
    svc = common.copy()
    for s in strategies:
        svc = svc & (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    scopes["service"] = df.index[svc]
    for sc, idx in scopes.items():
        g = df.loc[idx]
        b1e = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
        b1v = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        b1sec = b1e / b1v if b1v else None
        b1low = float((((g["b1_rule_f4"] > 0) & (g["b1_rule_f4"] / RATED < 0.5)).sum()
                       + ((g["b1_rule_f5"] > 0) & (g["b1_rule_f5"] / RATED < 0.5)).sum()) * STEP)
        blk = {"steps": int(len(g))}
        for s in strategies:
            E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h; V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
            low = float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                         + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
            sec = E / V if V else None
            blk[s] = {"energy_kwh": round(E, 2), "supply_m3": round(V, 1), "sec": (round(sec, 6) if sec else None),
                      "saving_pct": round((b1e - E) / b1e * 100, 4) if b1e else None,
                      "sec_reduction_pct": round((b1sec - sec) / b1sec * 100, 4) if b1sec and sec else None,
                      "gap_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
                      "low_min": round(low, 1),
                      "low_reduction_pct": round((b1low - low) / b1low * 100, 4) if b1low > 0 else "n/a",
                      "supply_diff_vs_b1_m3": round(V - b1v, 1)}
        out["scopes"][sc] = blk
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="dev"); ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--n", type=int, default=480)
    ap.add_argument("--cands", default=",".join(CANDS))
    a = ap.parse_args()
    cands = {k: CANDS[k] for k in a.cands.split(",") if k in CANDS}
    logp = str(ROOT / f"analysis/reports/b/multikpi_{a.window}_{a.start}_{a.n}.log")
    df, rt, fb = run(a.window, a.start, a.n, cands, logp)
    df.to_csv(ROOT / f"analysis/reports/b/multikpi_{a.window}_{a.start}_{a.n}_ledger.csv", index=False)
    rep = {"window": a.window, "start": a.start, "n": a.n, "cands": list(cands),
           "kpi": kpis(df, list(cands)),
           "runtime_ms": {s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
                              "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
                              "max": round(float(np.max(rt[s])) * 1000, 2)} for s in cands},
           "fallback": {s: int(fb[s]) for s in cands}}
    OUT = ROOT / f"analysis/reports/b/multikpi_{a.window}_{a.start}_{a.n}.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep["kpi"]["scopes"]["service"], ensure_ascii=False, indent=2))
    print("runtime_ms", json.dumps(rep["runtime_ms"], ensure_ascii=False))


if __name__ == "__main__":
    main()
