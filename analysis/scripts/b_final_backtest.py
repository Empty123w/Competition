"""B 组最终指标: 真实历史回测 (在 B5 harness 口径上新增 joint/mpc 候选)。

- 数据: A 组真实 SCADA 派生快照 + A6 真实滚动预测 (dev/valid/test 调度预测与评分真值)。
- 口径: full / common(共同可执行) / service(服务等价: flow>=obs-0.05)。
- 新增候选: joint(多目标 MPC: 压力+能效+低负荷+加载率均衡), mpc_hist(压力/能效).
- 输出: analysis/reports/b/final/{backtest_<window>.json,csv}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "analysis/scripts"))

import multikpi_harness as MH  # noqa: E402
from air_station.baseline import pipeline as PIPE  # noqa: E402
from air_station.baseline import rules as B1  # noqa: E402
from air_station.optimization import constraints as CONS  # noqa: E402
from air_station.optimization import contracts as C  # noqa: E402
from air_station.optimization import unit_commitment as UC  # noqa: E402
from air_station.optimization.forecast_adapter import load_forecast_bundle  # noqa: E402
from air_station.optimization.service import load_optimization_config, optimize_dispatch  # noqa: E402
from air_station.simulation import closed_loop as CL  # noqa: E402
from air_station.stage2 import joint_dispatch as JD  # noqa: E402

MS = MH.MS
STEP, RATED = 15, 268.9
HB = ROOT / "artifacts" / "handoff_b"
STRATEGIES = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")


PRESETS = {
    "full": dict(w_low=1.5, w_imbalance=40.0, w_pressure=5000.0, w_center=20000.0),
    "eco": dict(w_low=0.0, w_imbalance=0.0, w_pressure=0.0, w_center=0.0),
    "eco_low": dict(w_low=1.0, w_imbalance=0.0, w_pressure=0.0, w_center=0.0),
    "eco_low_p": dict(w_low=1.0, w_imbalance=0.0, w_pressure=800.0, w_center=2000.0),
    "full_minp": dict(w_low=1.5, w_imbalance=0.0, w_pressure=5000.0, w_center=20000.0),
    "eco_minp": dict(w_low=1.0, w_imbalance=0.0, w_pressure=0.0, w_center=0.0),
}


def _weights(kind, preset="full"):
    if kind == "joint":
        kw = dict(PRESETS[preset])
        if preset.endswith("_minp"):
            kw.pop("balance_mode", None)
            return JD.JointWeights(w_energy=1.0, w_switch=2.0, w_short=800.0,
                                   balance_mode="min_power", **kw)
        return JD.JointWeights(w_energy=1.0, w_switch=2.0, w_short=800.0,
                               balance_mode="spread", spread_tol=0.10, **kw)
    return JD.JointWeights(w_energy=1.0, w_low=0.0, w_imbalance=0.0, w_pressure=5000.0,
                           w_center=20000.0, w_switch=2.0, w_short=800.0)


def _load_bundle(window):
    if window == "test":
        hb = ROOT / "artifacts/handoff_b_test"
        scor = pd.read_parquet(hb / "forecast_test_scoring_actuals_calibrated.parquet")
        fn, split = "forecast_test_dispatch_calibrated.parquet", "test"
    else:
        hb = HB
        scor = pd.read_parquet(hb / "forecast_scoring_actuals_calibrated.parquet")
        fn = ("forecast_dev_dispatch_calibrated.parquet" if window == "dev"
              else "forecast_validation_dispatch_calibrated.parquet")
        split = "dev" if window == "dev" else "valid"
    b = load_forecast_bundle(hb / fn, scoring=scor, scoring_split=split)
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    return bd


def run(window, start, n, logpath, preset="full", basis="pred"):
    base = B1.load_config(B1.DEFAULT_CONFIG); con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    dev, st = PIPE.load_processed()
    bd = _load_bundle(window)
    if start or n:
        bd = bd.iloc[start:start + n].reset_index(drop=True) if n else bd.iloc[start:].reset_index(drop=True)
    cache = {}
    simp = CL.SimParams()
    W = {s: _weights(s, preset) for s in ("joint", "mpc_hist")}
    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices}
           for s in STRATEGIES}
    stt = {s: C.PolicyState() for s in STRATEGIES}
    rows = []; rt = {s: [] for s in STRATEGIES}; fb = {s: 0 for s in STRATEGIES}
    t0 = time.perf_counter()
    log = open(logpath, "a"); log.write(f"[start] window={window} n={len(bd)}\n"); log.flush()
    for i, (_, sr) in enumerate(bd.iterrows()):
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        p_now = float(hist.header_pressure_mpa) if hist.header_pressure_mpa is not None else 0.60
        r = {"t": ts, "obs": sr["obs"]}
        for s in STRATEGIES:
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = None; r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            tt = time.perf_counter()
            try:
                fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                if s == "b1_rule":
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif s == "b3_b4":
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif s in ("bal", "lrb_t10"):
                    req = [sr[f"ub{h}"] for h in (15, 30, 45, 60)]
                    cfg = (UC.UCConfig(demand_basis="upper", balanced=True) if s == "bal"
                           else UC.UCConfig(demand_basis="upper", spread_balance=True, spread_tol=0.10))
                    bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                 model_path=model, power_cap=1600.0, cfg=cfg, cache=cache)
                    if bb is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                else:  # joint / mpc_hist : 多目标 MPC, 用 A6 真实滚动预测 (因果)
                    ub = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    pred = (ub if basis == "upper" else [sr["pf15"], sr["pf30"], sr["pf45"], sr["pf60"]])
                    got = JD.mpc_decide(p_now, pred, ub, caps, con, cur_on, cur_flows,
                                        model, cache, simp, W[s], power_cap=1600.0)
                    if got is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = got["combo"]; al = got["alloc"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, pred[0], con.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = MH._p(flows, model, cache)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"; fb[s] += 1
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
        if (i + 1) % 500 == 0 or i == len(bd) - 1:
            log.write(f"[ckpt] {i+1}/{len(bd)} elapsed={time.perf_counter()-t0:.0f}s\n"); log.flush()
    log.close()
    return pd.DataFrame(rows), rt, fb


def kpis(df):
    dt_h = STEP / 60.0
    common = df[[f"{s}_status" for s in STRATEGIES]].eq("success").all(axis=1)
    for s in STRATEGIES:
        common = common & df[f"{s}_exec"]
    svc = common.copy()
    for s in STRATEGIES:
        svc = svc & (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    out = {"n_total": int(len(df)), "common_steps": int(common.sum()), "service_steps": int(svc.sum()),
           "scopes": {}}
    for sc, idx in (("common", df.index[common]), ("service", df.index[svc])):
        g = df.loc[idx]
        b1e = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
        b1v = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        b1sec = b1e / b1v if b1v else None
        b1low = float((((g["b1_rule_f4"] > 0) & (g["b1_rule_f4"] / RATED < 0.5)).sum()
                       + ((g["b1_rule_f5"] > 0) & (g["b1_rule_f5"] / RATED < 0.5)).sum()) * STEP)
        blk = {"steps": int(len(g))}
        for s in STRATEGIES:
            E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
            V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
            sec = E / V if V else None
            low = float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                         + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
            r4 = g[f"{s}_f4"] / RATED; r5 = g[f"{s}_f5"] / RATED
            both = (g[f"{s}_f4"] > 0) & (g[f"{s}_f5"] > 0)
            d3 = np.ones(len(g), dtype=bool)
            if both.any():
                d3[both.to_numpy()] = (np.abs(r4[both] - r5[both]) <= 0.10).to_numpy()
            blk[s] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1), "sec": round(sec, 6) if sec else None,
                      "sec_reduction_pct": round((b1sec - sec) / b1sec * 100, 3) if b1sec and sec else None,
                      "saving_pct": round((b1e - E) / b1e * 100, 3) if b1e else None,
                      "gap_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 1),
                      "low_min": round(low, 1),
                      "low_reduction_pct": round((b1low - low) / b1low * 100, 3) if b1low > 0 else None,
                      "d3_meet": round(float(d3.mean()), 4)}
        out["scopes"][sc] = blk
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="valid")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--preset", default="full")
    ap.add_argument("--basis", default="pred")
    a = ap.parse_args()
    (ROOT / "analysis/reports/b/final").mkdir(parents=True, exist_ok=True)
    logp = str(ROOT / f"analysis/reports/b/final/backtest_{a.window}.log")
    df, rt, fb = run(a.window, a.start, a.n, logp, a.preset, a.basis)
    df.to_csv(ROOT / f"analysis/reports/b/final/backtest_{a.window}_ledger.csv", index=False)
    rep = {"window": a.window, "n": int(len(df)), "strategies": list(STRATEGIES),
           "kpi": kpis(df),
           "runtime_ms": {s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
                              "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
                              "max": round(float(np.max(rt[s])) * 1000, 2)} for s in STRATEGIES},
           "fallback": {s: int(fb[s]) for s in STRATEGIES}}
    (ROOT / f"analysis/reports/b/final/backtest_{a.window}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rep["kpi"]["scopes"]["service"][k] for k in STRATEGIES}, ensure_ascii=False, indent=1))
    print("runtime", json.dumps(rep["runtime_ms"], ensure_ascii=False), "fb", rep["fallback"])


if __name__ == "__main__":
    main()
