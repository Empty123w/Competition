"""任务1/3/4: 压力评估可比性修复 + 需求/供气策略实验 + 约束来源与敏感性。

- 压力评测: 同一 fair 步集、同一初压 P0、同一 CRN 噪声; 记录 起/止压力 与 储气守恒。
- 需求策略候选 (UC 规划器): upper(基线) / pred / mid / resid(历史残差裕量)。
- 敏感性 (明确标记, 非正式成绩): 去备用(extra=0) / 最低稳定流量 60。
- 约束来源: 从配置读取并标注 real/engineering/pending。
输出: analysis/reports/b/pressure_supply_experiment.json (不覆盖旧结果)
"""
from __future__ import annotations

import dataclasses
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
OUT = ROOT / "analysis/reports/b/pressure_supply_experiment.json"
LED = ROOT / "analysis/reports/b/pressure_supply_experiment_ledger.csv"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_ps = importlib.util.spec_from_file_location("psim2", ROOT / "analysis/scripts/pressure_sim_dynamics_v2.py")
PS = importlib.util.module_from_spec(_ps); _ps.loader.exec_module(PS)

STEP, RATED, N, BLOCK_START = 15, 268.9, 480, 2400
P0 = 0.60


def _caps(min_flow=120.0):
    return {d: CONS.DeviceCapability(d, True, False, min_flow, RATED, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def _run(strategies, bd, dev, st, con, base, model):
    cache = {}
    init = PIPE.snapshot_from_frames(dev, st, bd["t"].iloc[0])
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in strategies}
    stt = {s: C.PolicyState() for s in strategies}
    rows = []
    for _, sr in bd.iterrows():
        t = pd.Timestamp(sr["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(dev, st, t)
        r = {"t": ts, "obs": sr["obs"], "pred": sr["pf15"]}
        for s, spec in strategies.items():
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = spec.get("caps") or CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = None; r[f"{s}_exec"] = False
            r[f"{s}_power"] = None; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            def _p_from(flows):
                tot = 0.0
                for d, q in flows.items():
                    if q and q > 0:
                        e = PERF.estimate_power(d, q, model_path=model)
                        if e["status"] != "success":
                            return None
                        tot += e["estimated_power_kw"]
                return tot
            try:
                if spec["kind"] == "b1":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = B1.decide(snap, fr, [], con, base, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                elif spec["kind"] == "b3":
                    fr = C.ForecastResult(ts, 15, sr["pf15"], None, sr["ub15"], "a6")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; trans = rr.transition_plan or []
                else:
                    basis = spec["basis"]
                    m = spec.get("margin", 0.0)
                    if basis == "upper":
                        req = [sr["ub15"], sr["ub30"], sr["ub45"], sr["ub60"]]
                    elif basis == "pred":
                        req = [sr["pf15"], sr["pf30"], sr["pf45"], sr["pf60"]]
                    elif basis == "mid":
                        req = [0.5 * (sr[f"pf{h}"] + sr[f"ub{h}"]) for h in (15, 30, 45, 60)]
                    elif basis == "resid":
                        req = [sr[f"pf{h}"] + m for h in (15, 30, 45, 60)]
                    cc = spec.get("con", con)
                    bb = UC.plan(req, caps, cc, cur_on, cur_flows, stt[s].last_switch_at, ts,
                                 model_path=model, power_cap=1600.0,
                                 cfg=UC.UCConfig(demand_basis="upper"), cache=cache)
                    if bb is None:
                        continue
                    combo = bb["seq"][0]; al = bb["alloc0"]; flows = al["flows"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], cc.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = _p_from(flows)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"
        rows.append(r)
    return pd.DataFrame(rows)


def _pressure_eval(df, s, fair_idx):
    """同一 fair 步集、同初压 P0、同 CRN; 返回压力指标与储气守恒。"""
    g = df.loc[fair_idx]
    dem = g["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
    sup = g[f"{s}_flow"].fillna(g["obs"]).fillna(0).to_numpy()
    noise = PS._noise(len(dem), 0, PS.SEED_BASE + 1)   # 0 噪声, 保证守恒可核对
    p, _ = PS._simulate(sup, dem, PS.K_VOL, PS.LEAK_PER_MIN, noise, closed_loop=True)
    net = float((sup - dem).sum()) * STEP            # 净供气 m3
    return {"compliance": round(float(((p >= 0.55) & (p <= 0.66)).mean()), 4),
            "below_band_min": int((p < 0.55).sum()) * STEP, "above_band_min": int((p > 0.66).sum()) * STEP,
            "p_start": P0, "p_end": round(float(p[-1]), 4),
            "net_supply_minus_demand_m3": round(net, 1),
            "delta_p_observed": round(float(p[-1] - P0), 4),
            "no_net_storage_depletion": bool((p[-1] >= P0 - 0.002) and (net >= -1e-6))}


def main():
    t0 = time.perf_counter()
    cfg_raw = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(cfg_raw)
    oc = load_optimization_config(); model = oc["paths"]["power_model"]
    dev, st = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue) if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    bd = pd.DataFrame({"t": grid}); bd["obs"] = [b.actual_at_target(t, 15) for t in grid]
    for h in (15, 30, 45, 60):
        bd[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
        bd[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
    # 历史残差裕量 (dev, 用于 resid 候选)
    resid = bd["obs"] - bd["pf15"]
    margin = float(np.nanquantile(resid, 0.9))
    bd = bd.iloc[BLOCK_START:BLOCK_START + N].reset_index(drop=True)

    con_norreserve = dataclasses.replace(con, reserve=C.ReservePolicy(mode="none", value=0.0, unit="percent"))
    strategies = {
        "b1_rule": {"kind": "b1"},
        "b3_b4": {"kind": "b3"},
        "uc_upper": {"kind": "uc", "basis": "upper"},
        "uc_pred": {"kind": "uc", "basis": "pred"},
        "uc_mid": {"kind": "uc", "basis": "mid"},
        "uc_resid": {"kind": "uc", "basis": "resid", "margin": margin},
        # 敏感性 (非正式): 明确标记
        "sens_norreserve": {"kind": "uc", "basis": "upper", "con": con_norreserve},
        "sens_minflow60": {"kind": "uc", "basis": "upper", "caps": _caps(60.0)},
    }
    df = _run(strategies, bd, dev, st, con, base=cfg_raw, model=model)
    df.to_csv(LED, index=False)
    dt_h = STEP / 60.0
    ok = df[[f"{s}_status" for s in strategies]].eq("success").all(axis=1)
    for s in strategies:
        ok = ok & df[f"{s}_exec"]
    fair_idx = df.index[ok]

    rep = {"block": {"start": str(bd["t"].iloc[0]), "n": int(len(bd)), "fair_steps": int(len(fair_idx))},
           "resid_margin_m3min": round(margin, 2),
           "constraint_sources": {
               "reserve_extra_5pct": {"value": con.reserve.value, "mode": con.reserve.mode,
                                      "source": cfg_raw["reserve"].get("source"), "CAL": "CAL-03"},
               "single_machine_threshold_m3min": round(RATED / 1.05, 2),
               "single_threshold_basis": "268.9/1.05 (= validated_max/(1+extra5%))",
               "min_stable_flow_m3min": con.validated_min_m3min,
               "min_stable_source": cfg_raw["capacity"]["source_flags"].get("validated_min"),
               "demand_basis": con.demand_basis, "demand_source": cfg_raw["demand"].get("source"),
               "CAL": {"demand": "CAL-02", "reserve": "CAL-03", "min_flow": "AS-02"}},
           "strategies": {}}
    gfair = df.loc[fair_idx]
    E = {s: float(gfair[f"{s}_power"].fillna(0).sum()) * dt_h for s in strategies}
    V = {s: float(gfair[f"{s}_flow"].fillna(0).sum()) * STEP for s in strategies}
    b1e = E["b1_rule"]
    for s in strategies:
        gg = df[(df[f"{s}_status"] == "success") & df[f"{s}_exec"]]
        bs = round(float((((gfair[f"{s}_f4"] > 0) & (gfair[f"{s}_f4"] / RATED < 0.5)).sum()
                          + ((gfair[f"{s}_f5"] > 0) & (gfair[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1)
        rep["strategies"][s] = {
            "exec_steps": int(len(gg)),
            "energy_kwh_fair": round(E[s], 2), "supply_m3_fair": round(V[s], 1),
            "sec_fair": round(E[s] / V[s], 6) if V[s] else None,
            "saving_vs_b1_pct": round((b1e - E[s]) / b1e * 100, 4) if b1e else None,
            "shortfall_vs_actual_m3_fair": round(float((gfair["obs"].fillna(0) - gfair[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
            "big_small_min_fair": bs,
            "pressure": _pressure_eval(df, s, fair_idx)}
    bs1 = rep["strategies"]["b1_rule"]["big_small_min_fair"]
    for s in strategies:
        rep["strategies"][s]["big_small_reduction_vs_b1_pct"] = (
            round((bs1 - rep["strategies"][s]["big_small_min_fair"]) / bs1 * 100, 4) if bs1 > 0 else "n/a")
    rep["b1_shortfall_m3_fair"] = rep["strategies"]["b1_rule"]["shortfall_vs_actual_m3_fair"]
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
