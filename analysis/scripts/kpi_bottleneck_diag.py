"""阶段二: 开发段小规模 KPI 瓶颈诊断 (校准 A6 数据)。

从开发段选取 4 个有代表性的连续时段 (正常/低负荷/峰值/预测误差大), 对
B1 / 冻结 B3+B4 / myopic / multi_step / DP v2 做连续回放, 定量诊断:
  1. 缺供原因 (预测低估 / 容量不足 / 过渡爬坡 / 启停锁定 / 策略分配)
  2. 低负荷时长原因 (最小稳定流量 / 双机分配 / 停止态)
  3. 压力不合格原因 (供气不足/过量/动态) —— 复用 pressure_sim_dynamics_v2
  4. multi_step vs myopic 真实增益
  5. 单次调度耗时分布
输出: analysis/reports/b/kpi_bottleneck_diag.json
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
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
OUT = ROOT / "analysis/reports/b/kpi_bottleneck_diag.json"
_s = importlib.util.spec_from_file_location("ms", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_s); _s.loader.exec_module(MS)
_s2 = importlib.util.spec_from_file_location("ms2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(MS2)
_ps = importlib.util.spec_from_file_location("psim2", ROOT / "analysis/scripts/pressure_sim_dynamics_v2.py")
PS = importlib.util.module_from_spec(_ps); _ps.loader.exec_module(PS)

STEP, RATED, MINF, CAP2 = 15, 268.9, 120.0, 2 * 268.9
STRAT = ("b1_rule", "b3_b4", "myopic", "multi_step", "v2")
W = 96   # 1 天 = 96 步


def _segments(g):
    """g: DataFrame indexed by order with obs, ub15."""
    obs = g["obs"].to_numpy(); err = np.abs(g["obs"] - g["ub15"]).to_numpy()
    med = np.nanmedian(obs)
    cand = []
    for name, key in (("normal", -np.abs(obs - med)), ("low", -obs), ("peak", obs), ("large_error", err)):
        # 滑窗均值最大化 key
        k = pd.Series(key).rolling(W).mean().to_numpy()
        start = int(np.nanargmax(k[:-1])) if np.isfinite(k[:-1]).any() else 0
        cand.append((name, start))
    return cand


def _diag_strategy_shortfall(row, s):
    """缺供原因分类。"""
    f = row.get(f"{s}_flow")
    if row.get(f"{s}_status") != "success":
        # 非成功: 容量不可行(ub不足) 或 其它
        if row["ub15"] * 1.05 > CAP2 + 1e-6:
            return "capacity_infeasible"
        return "infeasible_other"
    if not row.get(f"{s}_exec"):
        return "blocked_or_transition"
    if f is None or row["obs"] is None or f + 0.05 >= row["obs"]:
        return "none"
    if row["obs"] > row["ub15"] + 0.05:
        return "predicted_underestimate"
    return "allocation_short"


def _diag_lowload(row, s):
    """低负荷原因分类 (返回每类设备步数)。"""
    out = {"minflow_bound": 0, "rebalance_possible": 0, "low_other": 0}
    f4, f5 = row.get(f"{s}_f4", 0.0), row.get(f"{s}_f5", 0.0)
    req = row["ub15"]
    for f, other in ((f4, f5), (f5, f4)):
        if f and f > 0 and f / RATED < 0.5:
            if f <= MINF + 0.5:
                out["minflow_bound"] += 1
            elif other < RATED - 0.5:   # 另一机有裕量, 可再平衡
                out["rebalance_possible"] += 1
            else:
                out["low_other"] += 1
    return out


def run_segment(seg_df, bundle, device_df, station_df, oc, con, base_cfg):
    model = oc["paths"]["power_model"]; power_cap = 1600.0
    cache = {}

    def est(d, q):
        k = (d, round(float(q), 1))
        if k not in cache:
            r = PERF.estimate_power(d, float(q), model_path=model)
            cache[k] = r["estimated_power_kw"] if r["status"] == "success" else None
        return cache[k]

    t0 = pd.Timestamp(seg_df["t"].iloc[0])
    init = PIPE.snapshot_from_frames(device_df, station_df, t0)
    sim = {s: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices} for s in STRAT}
    stt = {s: C.PolicyState() for s in STRAT}
    rows = []; rt = {s: [] for s in STRAT}
    for _, segrow in seg_df.iterrows():
        t = pd.Timestamp(segrow["t"]); ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        obs = segrow["obs"]
        ub = {h: segrow[f"ub{h}"] for h in (15, 30, 45, 60)}
        pf = {h: segrow[f"pf{h}"] for h in (15, 30, 45, 60)}
        req_main = [ub[15], ub[30], ub[45], ub[60]]
        r = {"t": ts, "obs": (float(obs) if pd.notna(obs) else None), "ub15": ub[15],
             "ub45_avail": pd.notna(ub[45])}
        for s in STRAT:
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in ("4#", "5#") if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in ("4#", "5#")}
            r[f"{s}_status"] = "infeasible"; r[f"{s}_flow"] = r[f"{s}_power"] = None
            r[f"{s}_exec"] = False; r[f"{s}_f4"] = r[f"{s}_f5"] = 0.0
            r[f"{s}_blocked"] = False
            t1 = time.perf_counter()
            try:
                if s == "b1_rule":
                    fr = C.ForecastResult(ts, 15, pf[15], None, ub[15], "a6cal")
                    rr = B1.decide(snap, fr, [], con, base_cfg, stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - t1); continue
                    acts = rr.device_actions
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in acts if a.running_after}
                    p = 0.0
                    for d, q in flows.items():
                        e = est(d, q)
                        if e is None: p = None; break
                        p += e
                    trans = rr.transition_plan or []
                elif s == "b3_b4":
                    fr = C.ForecastResult(ts, 15, pf[15], None, ub[15], "a6cal")
                    rr = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if rr.status != "success":
                        r[f"{s}_status"] = rr.status; rt[s].append(time.perf_counter() - t1); continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in rr.device_actions if a.running_after}
                    acts = rr.device_actions; p = rr.estimated_power_kw; trans = rr.transition_plan or []
                else:
                    if s == "v2":
                        b = MS2.plan_v2(req_main, caps, con, est, cur_on, stt[s].last_switch_at, ts, power_cap, 0.0, MS.SWITCH_PENALTY_KWH)
                    else:
                        req = req_main[:1] if s == "myopic" else req_main
                        b = MS.plan(req, caps, con, model, cur_on, stt[s].last_switch_at, ts, power_cap)
                    if b is None:
                        rt[s].append(time.perf_counter() - t1); continue
                    combo = b["seq"][0]; al = b["alloc0"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    flows = al["flows"]; p = al["power_kw"]
                    trans = CONS.transition_feasibility(cur_on, combo, caps, req_main[0], con.reserve)[1]
                surge, blocked, _ = MS._surge(snap, acts, trans)
                ok = (not blocked) and all(x.get("supply_ok", True) for x in trans)
                r[f"{s}_status"] = "success"; r[f"{s}_flow"] = float(sum(flows.values()))
                r[f"{s}_power"] = (float(p) if p is not None else None)
                r[f"{s}_f4"] = float(flows.get("4#", 0.0)); r[f"{s}_f5"] = float(flows.get("5#", 0.0))
                r[f"{s}_exec"] = ok; r[f"{s}_blocked"] = blocked
                if ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                r[f"{s}_status"] = f"error:{type(e).__name__}"
            rt[s].append(time.perf_counter() - t1)
        rows.append(r)
    df = pd.DataFrame(rows)
    dt_h = STEP / 60.0
    summ = {"n_steps": len(df), "runtime_per_call_ms": {
        s: {"p50": round(float(np.percentile(rt[s], 50)) * 1000, 2),
            "p95": round(float(np.percentile(rt[s], 95)) * 1000, 2),
            "max": round(float(np.max(rt[s])) * 1000, 2)} for s in STRAT},
        "shortfall_cause": {}, "lowload_cause": {}, "energy_kwh": {}, "shortfall_m3": {},
        "big_small_min": {}, "ms_vs_myopic_pct": None}
    for s in STRAT:
        summ["shortfall_cause"][s] = {k: int(v) for k, v in
                                      df.apply(lambda x: _diag_strategy_shortfall(x, s), axis=1).value_counts().items()}
        ll = {"minflow_bound": 0, "rebalance_possible": 0, "low_other": 0}
        for _, row in df.iterrows():
            d = _diag_lowload(row, s)
            for k in ll: ll[k] += d[k]
        summ["lowload_cause"][s] = ll
        g = df[df[f"{s}_exec"]]
        summ["energy_kwh"][s] = round(float(g[f"{s}_power"].sum()) * dt_h, 2)
        summ["shortfall_m3"][s] = round(float((g["obs"].fillna(0) - g[f"{s}_flow"]).clip(lower=0).sum()) * STEP, 2)
        summ["big_small_min"][s] = round(float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                                                + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP), 1)
    if summ["energy_kwh"]["myopic"]:
        summ["ms_vs_myopic_pct"] = round((summ["energy_kwh"]["myopic"] - summ["energy_kwh"]["multi_step"]) / summ["energy_kwh"]["myopic"] * 100, 4)
    # 服务等价步 (全部可执行 且 均满足实测需求) 的能耗对比
    ok = df[[f"{s}_status" for s in STRAT]].eq("success").all(axis=1)
    for s in STRAT:
        ok = ok & df[f"{s}_exec"] & (df[f"{s}_flow"] >= df["obs"].fillna(-1) - 0.05)
    svc = df[ok]
    summ["service_equiv_steps"] = int(len(svc))
    if len(svc):
        E = {s: float(svc[f"{s}_power"].sum()) * dt_h for s in STRAT}
        summ["service_equiv_energy_kwh"] = {s: round(E[s], 2) for s in STRAT}
        b1 = E["b1_rule"]
        summ["service_equiv_saving_vs_b1_pct"] = {s: (round((b1 - E[s]) / b1 * 100, 4) if b1 else None) for s in STRAT}
    # 压力诊断 (闭环仿真; 供气=策略流量, 耗气=实测 obs)
    demand = df["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
    noise = PS._noise(len(demand), PS.NOISE, PS.SEED_BASE + 1)
    summ["pressure"] = {}
    for s in ("b1_rule", "b3_b4", "multi_step"):
        sup = df[f"{s}_flow"].fillna(df["obs"]).fillna(0).to_numpy()
        p, se = PS._simulate(sup, demand, PS.K_VOL, PS.LEAK_PER_MIN, noise, closed_loop=True)
        summ["pressure"][s] = {"compliance": round(float(((p >= 0.55) & (p <= 0.66)).mean()), 4),
                               "min_pressure": round(float(p.min()), 4),
                               "low_minutes": int((p < 0.55).sum()) * STEP,
                               "fluctuation_std": round(float(np.std(np.diff(p))), 5)}
    return df, summ


def main():
    t0 = time.perf_counter()
    base = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base)
    oc = load_optimization_config()
    device_df, station_df = PIPE.load_processed()
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    b = load_forecast_bundle(HB / "forecast_dev_dispatch_calibrated.parquet", scoring=scor, scoring_split="dev")
    grid = [t for t in sorted(b.by_issue)
            if t.minute % STEP == 0 and all(h in b.by_issue[t] for h in (15, 30, 45, 60))]
    base_df = pd.DataFrame({"t": grid})
    base_df["obs"] = [b.actual_at_target(t, 15) for t in grid]
    base_df["ub15"] = [b.forecast_result(t, 15).upper_bound for t in grid]
    for h in (15, 30, 45, 60):
        base_df[f"ub{h}"] = [b.forecast_result(t, h).upper_bound for t in grid]
        base_df[f"pf{h}"] = [b.forecast_result(t, h).predicted_flow for t in grid]
    rep = {"data": "A6 calibrated dev (15/30/45/60)", "window": "dev 2026-03-26~07-31",
           "segment_len_steps": W, "segments": {}}
    for name, start in _segments(base_df):
        seg = base_df.iloc[start:start + W].reset_index(drop=True)
        _, summ = run_segment(seg, b, device_df, station_df, oc, con, base)
        rep["segments"][name] = {"start": str(seg["t"].iloc[0]), **summ}
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
