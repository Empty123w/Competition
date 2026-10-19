"""独立候选版本 v2: 充分枚举可行负荷分配 + 联合目标 (电量 / 低负荷时长 / 启停代价)。

不做 v1 已完成的实验(不重跑); v1 结果作为对照复用。
纪律: 不放松 extra5%/压力/喘振约束; 不改最短启停; 不以增加缺供或频繁启停换取改善。

与 v1 的差异:
  1. 负载分配: 对双机组合**枚举分配比例**(GRID_RES), 取满足需求的最小电量分配;
     (v1 使用贪心边际单一分配)
  2. 目标: cost = 时域电量 + w_low * 低负荷步数折算 + w_switch * 切换次数;
     低负荷步 = 任一同行机组 load_ratio<0.5;
  3. 仅新增本脚本产物, 不改 v1 与冻结 B1-B4。

输出: analysis/reports/b/multistep_v2.json + multistep_v2_ledger.csv
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config

_spec = importlib.util.spec_from_file_location(
    "ms_v1", Path("analysis/scripts/multistep_optimization.py"))
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)

OUT = Path("analysis/reports/b/multistep_v2.json")
LED = Path("analysis/reports/b/multistep_v2_ledger.csv")
DEVICE_IDS = ("4#", "5#")
STEP = 15
HORIZON = 4
DAY = 1440
RATED = 268.9
GRID_RES = 0.02   # 分配枚举分辨率 (可调范围的 2%)
LOW_LIMIT = 0.5
VAL_START, VAL_END = "2026-08-01", "2026-08-20"
W_LOW_LIST = [0.0, 0.5, 2.0]   # kWh 等值/低负荷步


OVER_ALLOW = 80.0     # 允许的最大过供量(m3/min), 用于考察"脱离低负荷"的代价
TOTAL_GRID = 5        # 过供总量枚举数


def _mk_alloc(combo, required, caps, est_cached, w_low_eff):
    """枚举组合内可行分配(含过供), 取含低负荷惩罚代价最小的分配。

    w_low_eff 为"低负荷一步"的等效 kWh 代价 (已折算 dt); 用于在同等任务下
    权衡"多供气换高负荷"与"少供气但低负荷"。
    """
    if not combo:
        return {"ok": required <= 1e-6, "flows": {}, "power_kw": 0.0, "device_power": {}, "low": False}
    lo = {d: caps[d].min_flow_m3min for d in combo}
    hi = {d: caps[d].max_flow_m3min for d in combo}
    if sum(hi.values()) + 1e-9 < required:
        return {"ok": False}
    hi_sum = min(sum(hi.values()), required + OVER_ALLOW)
    lo_sum = sum(lo.values())
    best = None

    def _consider(flows):
        nonlocal best
        if flow_sum(flows) + 1e-6 < required:
            return
        if any(flows[d] < lo[d] - 1e-6 or flows[d] > hi[d] + 1e-6 for d in combo):
            return
        dp = {}
        for d in combo:
            dp[d] = est_cached(d, flows[d])
            if dp[d] is None:
                return
        p = sum(dp.values())
        low = any(flows[d] / RATED < LOW_LIMIT for d in combo)
        cost = p * (STEP / 60.0) + w_low_eff * (1 if low else 0)
        cand = {"ok": True, "flows": {d: round(flows[d], 2) for d in combo},
                "power_kw": p, "device_power": dp, "low": low, "_cost": cost}
        if best is None or cost < best["_cost"] - 1e-9:
            best = cand

    if len(combo) == 1:
        d = combo[0]
        for tot in np.linspace(max(required, lo_sum), hi_sum, TOTAL_GRID):
            _consider({d: float(tot)})
    else:
        d1, d2 = combo
        for tot in np.linspace(max(required, lo_sum), hi_sum, TOTAL_GRID):
            extra = tot - lo_sum
            for r in np.linspace(0.0, 1.0, int(1 / GRID_RES) + 1):
                e1 = min(extra * r, hi[d1] - lo[d1])
                e2 = extra - e1
                if e2 < -1e-6 or e2 > hi[d2] - lo[d2] + 1e-6:
                    continue
                _consider({d1: lo[d1] + e1, d2: lo[d2] + e2})
    return best if best else {"ok": False}


def flow_sum(flows):
    return float(sum(flows.values()))


def plan_v2(required_seq, caps, con, est_cached, cur_on, last_switch, as_of, power_cap, w_low, w_switch):
    H = len(required_seq)
    step_opts = []
    for k in range(H):
        opts = {}
        for combo in CONS.COMBINATIONS:
            a = CONS.assess_combination(combo, caps, required_seq[k], con.reserve)
            if not a.feasible:
                continue
            al = _mk_alloc(combo, required_seq[k], caps, est_cached, w_low)
            if not al.get("ok"):
                continue
            if power_cap > 0 and any((al["flows"].get(x, 0.0) > 0 and est_cached(x, al["flows"][x]) > power_cap + 1e-6)
                                     for x in combo):
                continue
            opts[combo] = al
        step_opts.append(opts)
    if any(len(o) == 0 for o in step_opts):
        return None
    best = None
    dt_h = STEP / 60.0
    for seq in itertools.product(*[list(o.keys()) for o in step_opts]):
        if not MS._can_switch_at(cur_on, seq[0], as_of, last_switch, con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, seq[0], caps, required_seq[0], con.reserve)
        if not ok:
            continue
        prev, switches, low_steps = set(cur_on), 0, 0
        for k in range(H):
            switches += len(MS._switch_events(prev, seq[k]))
            prev = set(seq[k])
            if step_opts[k][seq[k]]["low"]:
                low_steps += 1
        if switches > con.max_switches_per_hour:
            continue
        energy = sum(step_opts[k][seq[k]]["power_kw"] for k in range(H)) * dt_h
        step_low_cost = sum(w_low for k in range(H) if step_opts[k][seq[k]]["low"])
        cost = energy + step_low_cost + w_switch * switches
        key = (round(cost, 6), switches, tuple(seq))
        if best is None or key < best[0]:
            best = (key, {"seq": [tuple(c) for c in seq], "switches": switches, "low_steps": low_steps,
                          "energy": round(energy, 4), "alloc0": step_opts[0][seq[0]],
                          "cost": round(cost, 6)})
    return best[1] if best else None


def main():
    t0 = time.perf_counter()
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    power_cap = float(oc.get("dispatch", {}).get("power_cap_kw_per_device", 0.0) or 0.0)
    device_df, station_df = PIPE.load_processed()
    flow_at = dict(zip(station_df["time"].astype(str), station_df["total_flow_m3_min"]))

    cache = {}

    def est(d, q):
        key = (d, round(float(q), 1))
        if key not in cache:
            r = PERF.estimate_power(d, float(q), model_path=model)
            cache[key] = r["estimated_power_kw"] if r["status"] == "success" else None
        return cache[key]

    times = sorted(station_df["time"])
    val = [t for t in times if VAL_START <= str(t)[:10] <= VAL_END][::STEP]
    hist0 = PIPE.snapshot_from_frames(device_df, station_df, val[0])
    planners = ["b1_rule"] + [f"v2_w{w}" for w in W_LOW_LIST]
    sim = {p: {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in hist0.devices}
           for p in planners}
    stt = {p: C.PolicyState() for p in planners}
    rows = []
    for t in val:
        ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        obs = float(flow_at.get(ts, np.nan))
        season = [flow_at.get(str(t - pd.Timedelta(minutes=DAY) + pd.Timedelta(minutes=STEP * k)), np.nan)
                  for k in range(HORIZON)]
        row = {"t": ts, "obs_flow": obs, "required_forecast": float(season[0]) if np.isfinite(season[0]) else None}
        for p in planners:
            snap = MS._sim_snapshot(hist, sim[p], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[p])
            cur_on = {d for d in DEVICE_IDS if sim[p][d]["running"]}
            cur_flows = {d: sim[p][d]["flow"] for d in DEVICE_IDS}
            row[f"{p}_status"] = "infeasible"
            row[f"{p}_flow"] = row[f"{p}_power"] = None
            row[f"{p}_blocked"] = row[f"{p}_exec"] = False
            row[f"{p}_f4"] = row[f"{p}_f5"] = 0.0
            if p == "b1_rule":
                sf = season[0] if np.isfinite(season[0]) else obs
                if not np.isfinite(sf):
                    continue
                fc = C.ForecastResult(ts, STEP, float(sf), float(sf), float(sf), "seasonal")
                r = B1.decide(snap, fc, [], con, base_cfg, stt[p])
                if r.status != "success":
                    continue
                flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in r.device_actions if a.running_after}
                power = 0.0
                bad = False
                for d, q in flows.items():
                    e = est(d, q)
                    if e is None:
                        bad = True
                        break
                    power += e
                if bad:
                    continue
                acts = r.device_actions
                trans = r.transition_plan or []
            else:
                if not all(np.isfinite(season)):
                    continue
                req = [float(x) for x in season]
                w = float(p.split("w")[-1])
                best = plan_v2(req, caps, con, est, cur_on, stt[p].last_switch_at, ts, power_cap, w, MS.SWITCH_PENALTY_KWH)
                if best is None:
                    continue
                combo = best["seq"][0]
                al = best["alloc0"]
                acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                flows = al["flows"]
                power = al["power_kw"]
                trans = CONS.transition_feasibility(cur_on, combo, caps, req[0], con.reserve)[1]
            lvl, blocked, _ = MS._surge(snap, acts, trans)
            trans_ok = all(s.get("supply_ok", True) for s in trans)
            exec_ok = (not blocked) and trans_ok
            row[f"{p}_status"] = "success"
            row[f"{p}_flow"] = sum(flows.values())
            row[f"{p}_power"] = power
            row[f"{p}_f4"] = float(flows.get("4#", 0.0))
            row[f"{p}_f5"] = float(flows.get("5#", 0.0))
            row[f"{p}_blocked"] = blocked
            row[f"{p}_exec"] = exec_ok
            row[f"{p}_met_actual"] = bool(exec_ok and np.isfinite(obs) and sum(flows.values()) + 0.05 >= obs)
            if exec_ok:
                MS._apply(sim[p], acts, ts, stt[p])
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(LED, index=False)
    dt_h = STEP / 60.0

    def lowmin(g, p):
        return int(((g[f"{p}_f4"] > 0) & (g[f"{p}_f4"] / RATED < LOW_LIMIT)).sum()
                   + ((g[f"{p}_f5"] > 0) & (g[f"{p}_f5"] / RATED < LOW_LIMIT)).sum()) * STEP

    rep = {"protocol": {"window": f"{VAL_START}~{VAL_END}", "step_minutes": STEP, "horizon_steps": HORIZON,
                        "grid_res": GRID_RES, "w_low_list": W_LOW_LIST, "switch_penalty": MS.SWITCH_PENALTY_KWH,
                        "reserve": "frozen extra 5%", "uses_B5_final_window": False,
                        "evidence_scope": "开发/验证窗口 + 仅过去信息预测; 新算法独立版本, 非达标证据"}}
    per = {}
    for p in planners:
        g = df[(df[f"{p}_status"] == "success") & df[f"{p}_exec"]]
        E = float(g[f"{p}_power"].sum()) * dt_h
        V = float(g[f"{p}_flow"].sum()) * STEP
        per[p] = {"evaluable_steps": int(len(g)), "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "sec": round(E / V, 6) if V else None,
                  "shortfall_vs_observed_m3": round(float((g["obs_flow"] - g[f"{p}_flow"]).clip(lower=0).sum()) * STEP, 2),
                  "low_load_minutes": lowmin(g, p)}
    rep["per_planner"] = per
    # 公平步集 (b1 与各 v2 均可执行)
    ok = df["b1_rule_status"].eq("success") & df["b1_rule_exec"]
    for p in planners:
        if p != "b1_rule":
            ok = ok & df[f"{p}_status"].eq("success") & df[f"{p}_exec"]
    fair = df[ok]
    rep["fair_steps"] = int(len(fair))
    if len(fair):
        E = {p: float(fair[f"{p}_power"].sum()) * dt_h for p in planners}
        rep["fair_energy_kwh"] = {p: round(E[p], 2) for p in E}
        rep["fair_low_load_minutes"] = {p: lowmin(fair, p) for p in planners}
        b1 = E["b1_rule"]
        bs1 = rep["fair_low_load_minutes"]["b1_rule"]
        for p in planners:
            if p == "b1_rule":
                continue
            rep[f"{p}_energy_saving_vs_b1_pct"] = round((b1 - E[p]) / b1 * 100, 4) if b1 else None
            rep[f"{p}_low_load_reduction_vs_b1_pct"] = (round((bs1 - rep["fair_low_load_minutes"][p]) / bs1 * 100, 4)
                                                        if bs1 > 0 else "n/a (baseline 0)")
    rep["n_steps"] = int(len(df))
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
