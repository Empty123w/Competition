"""Task1 误差归因 + Task3 Adaptive Robust Dispatch 开发段实验 (3 窗口, <=12 候选)。

窗口: dev 校准 A6 固定连续窗口 (正常/低负荷/峰值-大误差), 各 240 步。
候选: B1 / B3+B4 / cand / ARD 变体。
因果残差: 仅用 target<=as_of 的已实现 (actual-pred)。
压力评测: 统一 P0=0.60、同 fair 步、CRN。
输出: analysis/reports/b/ard_experiment.json
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
from air_station.optimization import adaptive_robust as ARD
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization import unit_commitment as UC
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/ard_experiment.json"
LED = ROOT / "analysis/reports/b/ard_experiment_ledger.csv"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
STEP, RATED, W = 15, 268.9, 240
P0 = 0.60

# 6 组候选 (任务二): B1 / B3+B4 / cand / ARD因果误差修正 / ARD动态裕量 / ARD动态裕量+峰值
ARD_CONFIGS = {
    "ard_err": ARD.ARDConfig(method="ewma", alpha=0.15, cap_to_upper=False),   # 因果误差修正(不加 upper 帽)
    "ard_dyn": ARD.ARDConfig(method="quantile", q=0.80, cap_to_upper=True),    # 动态裕量(逐时域 clamp 到 upper)
    "ard_dyn_peak": ARD.ARDConfig(method="quantile", q=0.80, cap_to_upper=True, peak_lead=True),
}


def _pick_windows(bd):
    """确定性固定窗口规则 (不按可行性挑选, 保留全部不可行步):
    normal = 均值最接近中位数的 W 步; low = 均值最低; peak = 均值最高。"""
    obs = bd["obs"].to_numpy()
    med = np.nanmedian(obs)
    out = {}
    for name, key in (("normal", -np.abs(obs - med)), ("low", -obs), ("peak", obs)):
        k = np.array(pd.Series(key).rolling(W).mean().to_numpy(), dtype=float)
        k[np.isnan(k)] = -np.inf
        out[name] = int(np.argmax(k[:-1]))
    return out


def _run_window(bd, strategies, dev, st, con, base, model):
    cache = {}
    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in strategies}
    stt = {s: C.PolicyState() for s in strategies}
    est = {s: ARD.ResidualEstimator(spec["cfg"]) for s, spec in strategies.items() if spec["kind"] == "ard"}
    fb = {s: 0 for s in strategies}
    rt = {s: [] for s in strategies}
    rows = []
    for i, (_, sr) in enumerate(bd.iterrows()):
        t = pd.Timestamp(sr["t"]); ts = str(t)
        # 因果残差更新: 用上一步已实现 (target<=t)
        if i > 0:
            for s in strategies:
                if strategies[s]["kind"] == "ard":
                    est[s].update(bd["t"].iloc[i - 1], bd["obs"].iloc[i - 1], bd["pf15"].iloc[i - 1])
        hist = PIPE.snapshot_from_frames(dev, st, t)
        r = {"t": ts, "obs": sr["obs"]}
        for s, spec in strategies.items():
            kind = spec["kind"]
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
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif kind == "b3":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - tt); continue
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
                else:  # ard
                    preds = [sr["pf15"], sr["pf30"], sr["pf45"], sr["pf60"]]
                    ubs = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    if spec.get("basis") == "upper":
                        req = ubs
                        bb = UC.plan(req, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                     model_path=model, power_cap=1600.0, cfg=UC.UCConfig(demand_basis="upper"), cache=cache)
                        margins = [0, 0, 0, 0]
                    else:
                        bb = ARD.plan_ard(preds, ubs, caps, con, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                          est[s], spec["cfg"], model_path=model, power_cap=1600.0, cache=cache)
                        req = bb["demand_seq"] if bb else preds; margins = bb["margins"] if bb else [0, 0, 0, 0]
                    if bb is None:
                        fb[s] += 1; rt[s].append(time.perf_counter() - tt); continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = _power(flows, model, cache)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"; fb[s] += 1
            rt[s].append(time.perf_counter() - tt)
        rows.append(r)
    return pd.DataFrame(rows), rt, fb


def _power(flows, model, cache):
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


def _pressure(df, s, fair_idx):
    g = df.loc[fair_idx]
    dem = g["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
    sup = g[f"{s}_flow"].fillna(g["obs"]).fillna(0).to_numpy()
    p = [P0]; k_vol, leak, p_env = 23700.0, 0.001, 0.55
    for i in range(len(dem)):
        pv = p[-1] + STEP * ((sup[i] - dem[i]) / k_vol - leak * (p[-1] - p_env))
        p.append(min(max(pv, 0.40), 0.75))
    p = np.array(p[1:])
    return {"compliance": round(float(((p >= 0.55) & (p <= 0.66)).mean()), 4),
            "p_end": round(float(p[-1]), 4), "net_m3": round(float((sup - dem).sum()) * STEP, 1)}


def _blk(df, strategies, idx, dt_h):
    b1e = float(df.loc[idx, "b1_rule_power"].fillna(0).sum()) * dt_h
    b1v = float(df.loc[idx, "b1_rule_flow"].fillna(0).sum()) * STEP
    per = {}
    for s in strategies:
        g = df.loc[idx]
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        bs = round(float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                          + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1)
        per[s] = {"energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "gap_vs_actual_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
                  "sec": round(E / V, 6) if V else None,
                  "saving_vs_b1_pct": round((b1e - E) / b1e * 100, 4) if b1e else None,
                  "supply_diff_vs_b1_m3": round(V - b1v, 1),
                  "big_small_min": bs}
    bs1 = per["b1_rule"]["big_small_min"]
    for s in strategies:
        per[s]["big_small_reduction_vs_b1_pct"] = (round((bs1 - per[s]["big_small_min"]) / bs1 * 100, 4) if bs1 > 0 else "n/a")
    return per


def _summ(df, strategies, name):
    dt_h = STEP / 60.0
    res = {"window": name, "n": int(len(df))}
    # 全窗口 (保留不可行步; 未执行步供气/电量为 0)
    res["full"] = _blk(df, strategies, df.index, dt_h)
    # 共同可执行子集
    ok = df[[f"{s}_status" for s in strategies]].eq("success").all(axis=1)
    for s in strategies:
        ok = ok & df[f"{s}_exec"]
    fair = df.index[ok]
    res["common_steps"] = int(len(fair))
    res["common"] = _blk(df, strategies, fair, dt_h) if len(fair) else {}
    for s in strategies:
        if len(fair):
            res["common"][s]["pressure"] = _pressure(df, s, fair)
        res["full"][s]["non_exec_steps"] = int((~(df[f"{s}_exec"] & df[f"{s}_status"].eq("success"))).sum())
    return res


def task1_attribution(bd, con, base, model):
    """B3(上界) 全 dev: 缺供/过供归类。"""
    cache = {}; init = PIPE.snapshot_from_frames(dev_g, st_g, bd["t"].iloc[0])
    sim = {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices}
    stt = C.PolicyState()
    out = {"pred_underestimate_shortfall": 0, "capacity_infeasible": 0, "over_conservative_oversupply": 0,
           "shortfall_excess_over_b1": 0, "steps": 0, "shortfall_m3": 0.0, "oversupply_m3": 0.0}
    for _, sr in bd.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        snap = MS._sim_snapshot(PIPE.snapshot_from_frames(dev_g, st_g, t), sim, ts)
        caps = CONS.build_capabilities(con, snap, [], stt)
        cur_on = {d for d in ("4#", "5#") if sim[d]["running"]}
        ub = sr["ub15"]; obs = sr["obs"]
        out["steps"] += 1
        if obs is not None and np.isfinite(obs):
            if ub * 1.05 > 2 * RATED:
                out["capacity_infeasible"] += 1
            elif obs > ub + 0.05:
                out["pred_underestimate_shortfall"] += 1
                out["shortfall_m3"] += (obs - ub) * STEP
            elif ub - obs > 20:
                out["over_conservative_oversupply"] += 1
                out["oversupply_m3"] += (ub - obs) * STEP
    return out


dev_g, st_g = None, None


def main():
    global dev_g, st_g
    t0 = time.perf_counter()
    base = B1.load_config(B1.DEFAULT_CONFIG); con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    dev_g, st_g = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]

    strategies = {"b1_rule": {"kind": "b1"}, "b3_b4": {"kind": "b3"}, "cand": {"kind": "cand"}}
    for name, cfg in ARD_CONFIGS.items():
        strategies[name] = {"kind": "ard", "cfg": cfg, "basis": ("upper" if name == "ard_upper" else "ard")}
    assert len(strategies) <= 12

    rep = {"task1_attribution": task1_attribution(bd, con, base, model), "windows": {}, "runtime_ms": {}, "fallback": {}}
    picks = _pick_windows(bd)
    allrt = {s: [] for s in strategies}; allfb = {s: 0 for s in strategies}
    led = []
    for wname, start in picks.items():
        sub = bd.iloc[start:start + W].reset_index(drop=True)
        df, rt, fb = _run_window(sub, strategies, dev_g, st_g, con, base, model)
        df.insert(0, "window", wname)
        led.append(df)
        rep["windows"][wname] = {"start": str(sub["t"].iloc[0]), **_summ(df, strategies, wname)}
        for s in strategies:
            allrt[s] += rt[s]; allfb[s] += fb[s]
    pd.concat(led, ignore_index=True).to_csv(LED, index=False)
    rep["runtime_ms"] = {s: {"p50": round(float(np.percentile(allrt[s], 50)) * 1000, 2),
                             "p95": round(float(np.percentile(allrt[s], 95)) * 1000, 2),
                             "max": round(float(np.max(allrt[s])) * 1000, 2)} for s in strategies}
    rep["fallback"] = {s: int(allfb[s]) for s in strategies}
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"task1": rep["task1_attribution"], "runtime_s": rep["runtime_s"],
                      "fallback": rep["fallback"]}, ensure_ascii=False, indent=2))
    for wname in rep["windows"]:
        w = rep["windows"][wname]
        print("\n==", wname, w["start"], "n", w["n"], "common", w["common_steps"])
        for scope in ("full", "common"):
            print("  --", scope)
            for s in strategies:
                x = w[scope].get(s)
                if not x:
                    continue
                pr = x.get("pressure", {})
                print(f"    {s:14s} save% {x['saving_vs_b1_pct']} gap {x['gap_vs_actual_m3']} sec {x['sec']} low% {x['big_small_reduction_vs_b1_pct']} dV {x['supply_diff_vs_b1_m3']} pcomp {pr.get('compliance')} pend {pr.get('p_end')}")


if __name__ == "__main__":
    main()
