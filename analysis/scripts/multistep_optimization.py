"""B 完整多时间步优化实验 (DP/枚举时域规划) — 替代旧 E2 horizon-max 启发式。

不改冻结 B1-B5; 不在 B5 窗口 (2026-08-21~09-11) 调参; 不在冻结 src 内改算法。
本脚本为**独立实验**: 开发/验证窗口 (2026-08-01~08-20) 连续状态回放。

完整多时间步规划器 (exact DP/枚举, 非启发式):
  - 在时域 H 步 (步长 15 min, H=4 => 60 min) 内枚举 4^H 组合序列;
  - 每步满足 容量+备用 (frozen extra5%) 与 单机功率上限;
  - 首步切换遵守 最短运行/停机 锁定 (min_run/min_stop=15 min);
  - 时域内机器级切换事件 <= max_switches_per_hour=2;
  - 目标 = 时域总电量 + 切换惩罚(工程假设) 之和最小。

对比 (同一冻结评价模型/同一连续状态口径):
  - b1_rule   : B1 冻结规则 (季节日=仅过去信息预测)
  - myopic    : 同一规划器 H=1
  - multi_step: 完整多时间步 H=4 (季节日预测, 可部署形态)
  - oracle    : H=4 使用**真实未来**需求 (非部署, 仅诊断上界)

必须补齐 (旧 E2 缺失):
  - 完整供气账本: 每步 计划供气(时域)/执行供气/电量/SEC;
  - 候选 B4 喘振评估: 对每步实际执行候选运行 assess_surge_risk。

输出:
  analysis/reports/b/multistep_optimization.json
  analysis/reports/b/multistep_optimization_ledger.csv
"""
from __future__ import annotations

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

OUT = Path("analysis/reports/b/multistep_optimization.json")
LED = Path("analysis/reports/b/multistep_optimization_ledger.csv")
TRACE = Path("analysis/reports/b/multistep_extra_steps_trace.csv")

DEVICE_IDS = ("4#", "5#")
STEP = 15                 # min, 与 E1/E2 验证窗口口径一致
HORIZON = 4               # 时域步数 => 60 min
DAY = 1440
SWITCH_PENALTY_KWH = 2.0  # 工程假设: 每次机器级切换等效能耗 (标注 engineering)
VAL_START, VAL_END = "2026-08-01", "2026-08-20"


# ------------------------------------------------------------------ #
# 完整多时间步规划器 (exact enumeration over horizon)
# ------------------------------------------------------------------ #
def _switch_events(prev_on: set, combo) -> list:
    return [d for d in DEVICE_IDS if (d in combo) != (d in prev_on)]


def _can_switch_at(cur_on: set, combo, as_of, last_switch: dict, con) -> bool:
    t = pd.Timestamp(as_of)
    for d in DEVICE_IDS:
        before, after = d in cur_on, d in combo
        if before == after:
            continue
        last = last_switch.get(d)
        if last is None:
            continue
        delta = (t - pd.Timestamp(last)).total_seconds() / 60.0
        if after and delta < con.min_stop_minutes:
            return False
        if (not after) and delta < con.min_run_minutes:
            return False
    return True


def plan(required_seq, caps, con, model, cur_on, cur_last_switch, as_of, power_cap) -> dict | None:
    """枚举时域 H 步组合序列, 返回最小代价可行解 (含首步与计划账本)。"""
    H = len(required_seq)
    step_opts = []
    for k in range(H):
        d = required_seq[k]
        opts = {}
        for combo in CONS.COMBINATIONS:
            a = CONS.assess_combination(combo, caps, d, con.reserve)
            if not a.feasible:
                continue
            alloc = OPT.allocate_flow(list(combo), d, caps, model)
            if not alloc["ok"]:
                continue
            if power_cap > 0 and any(alloc["device_power"].get(x, 0.0) > power_cap + 1e-6 for x in combo):
                continue
            opts[combo] = alloc
        step_opts.append(opts)
    if any(len(o) == 0 for o in step_opts):
        return None

    best = None
    dt_h = STEP / 60.0
    for seq in itertools.product(*[list(o.keys()) for o in step_opts]):
        if not _can_switch_at(cur_on, seq[0], as_of, cur_last_switch, con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, seq[0], caps, required_seq[0], con.reserve)
        if not ok:
            continue
        prev, switches = set(cur_on), 0
        for k in range(H):
            ev = _switch_events(prev, seq[k])
            switches += len(ev)
            prev = set(seq[k])
        if switches > con.max_switches_per_hour:
            continue
        energy = sum(step_opts[k][seq[k]]["power_kw"] for k in range(H)) * dt_h
        cost = energy + SWITCH_PENALTY_KWH * switches
        key = (round(cost, 6), switches, tuple(seq))
        if best is None or key < best[0]:
            best = (key, {"seq": [tuple(c) for c in seq], "switches": switches,
                          "horizon_energy_kwh": round(energy, 4),
                          "cost": round(cost, 6),
                          "alloc0": step_opts[0][seq[0]],
                          "horizon_plan_supply_m3": round(
                              sum(step_opts[k][seq[k]]["total_flow_m3min"] for k in range(H)) * STEP, 3)})
    return best[1] if best else None


# ------------------------------------------------------------------ #
# 连续状态回放
# ------------------------------------------------------------------ #
def _sim_snapshot(hist_snap, sim, as_of):
    devs = []
    for d in hist_snap.devices:
        s = sim.get(d.device_id, {"running": d.running, "flow": d.flow_m3_min})
        devs.append(C.DeviceSnapshot(d.device_id, bool(s["running"]), s.get("flow"), None,
                                     d.discharge_pressure_mpa, d.igv_percent,
                                     d.bov_closed_percent, d.quality_valid, "replay_sim"))
    return C.StationSnapshot(as_of, hist_snap.header_pressure_mpa, devs, "replay_sim")


def _actions(combo, alloc, caps, cur_on, current_flows):
    acts = []
    for d in DEVICE_IDS:
        on, was = d in combo, d in cur_on
        if on:
            flow = float(alloc["flows"].get(d, caps[d].min_flow_m3min))
            prev = float(current_flows.get(d, 0.0) or 0.0)
            action = "start" if not was else ("adjust" if abs(flow - prev) > 1.0 else "hold")
            acts.append(C.DeviceAction(d, action, round(flow, 2), None, True,
                                       estimated_power_kw=alloc["device_power"].get(d)))
        else:
            acts.append(C.DeviceAction(d, "stop" if was else "hold", 0.0, 0.0, False,
                                       estimated_power_kw=0.0))
    return acts


def _apply(sim, actions, ts, state):
    switched = []
    for a in actions:
        sim[a.device_id] = {"running": bool(a.running_after),
                            "flow": (a.target_flow_m3_min or 0.0) if a.running_after else 0.0}
        if a.action in ("start", "stop"):
            state.last_switch_at[a.device_id] = ts
            state.recent_switch_times.append(ts)
            switched.append(a.device_id)
    return switched


def _surge(snap, actions, transition_plan):
    res = C.DispatchResult(status=C.STATUS_SUCCESS, as_of=snap.as_of, policy_version="b-exp-multistep")
    res.device_actions = actions
    res.transition_plan = transition_plan
    try:
        from air_station.safety import surge as SURGE
        a = SURGE.assess_surge_risk(snap, res)
        return SURGE.worst_level(a), bool(a.safety_blocked), a.counts
    except Exception as e:  # noqa
        return "unknown", True, {"unknown": 1, "message": str(e)}


def replay():
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    power_cap = float(oc.get("dispatch", {}).get("power_cap_kw_per_device", 0.0) or 0.0)
    device_df, station_df = PIPE.load_processed()
    flow_at = dict(zip(station_df["time"].astype(str), station_df["total_flow_m3_min"]))

    times = sorted(station_df["time"])
    val = [t for t in times if VAL_START <= str(t)[:10] <= VAL_END][::STEP]

    planners = ("b1_rule", "myopic", "multi_step", "oracle")
    sim = {}
    st = {}
    hist0 = PIPE.snapshot_from_frames(device_df, station_df, val[0])
    for p in planners:
        sim[p] = {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in hist0.devices}
        st[p] = C.PolicyState()

    rows = []
    for t in val:
        ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        obs_flow = float(flow_at.get(ts, np.nan))
        # 预测 (仅过去信息): 季节日同时刻; oracle 用真实未来 (诊断)
        season = [flow_at.get(str(t - pd.Timedelta(minutes=DAY) + pd.Timedelta(minutes=STEP * k)), np.nan)
                  for k in range(HORIZON)]
        actual = [flow_at.get(str(t + pd.Timedelta(minutes=STEP * k)), np.nan) for k in range(HORIZON)]
        row = {"t": ts, "obs_flow": obs_flow, "obs_power": None, "obs_pressure": None}
        # 时间对齐记录: 预测发出时刻 t; 目标供气区间 [t, t+STEP); 预测=前一日同时刻; 实测需求=obs_flow(t)
        row["required_forecast"] = float(season[0]) if np.isfinite(season[0]) else None
        row["required_actual"] = obs_flow
        sta = station_df[station_df["time"] == t]
        if len(sta):
            row["obs_power"] = float(sta["total_power_kw"].iloc[0])
            row["obs_pressure"] = float(sta["header_pressure_mpa"].iloc[0])

        for p in planners:
            snap = _sim_snapshot(hist, sim[p], ts)
            caps = CONS.build_capabilities(con, snap, [], st[p])
            cur_on = {d for d in DEVICE_IDS if sim[p][d]["running"]}
            cur_flows = {d: sim[p][d]["flow"] for d in DEVICE_IDS}
            row[f"{p}_cur_on"] = "+".join(sorted(cur_on)) if cur_on else "off"
            row[f"{p}_status"], row[f"{p}_flow"], row[f"{p}_power"] = "infeasible", None, None
            row[f"{p}_n"], row[f"{p}_surge"], row[f"{p}_blocked"], row[f"{p}_exec"] = 0, "n/a", False, False
            row[f"{p}_hplan_supply"] = None

            if p == "b1_rule":
                sf = season[0] if np.isfinite(season[0]) else obs_flow
                if not np.isfinite(sf):
                    continue
                fc = C.ForecastResult(ts, STEP, float(sf), float(sf), float(sf), "seasonal")
                r = B1.decide(snap, fc, [], con, base_cfg, st[p])
                if r.status != "success":
                    row[f"{p}_status"] = r.status
                    continue
                acts = r.device_actions
                flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in acts if a.running_after}
                combo = tuple(flows.keys())
                # B1 关闭自有功率估计: 用同一冻结功率模型按其流量估算, 保证口径一致
                alloc_power = 0.0
                for d, q in flows.items():
                    er = PERF.estimate_power(d, q, model_path=model)
                    if er["status"] != "success":
                        alloc_power = None
                        break
                    alloc_power += er["estimated_power_kw"]
                if alloc_power is None:
                    row[f"{p}_status"] = "model_unavailable"
                    continue
                trans = r.transition_plan or []
                status = "success"
            else:
                req = season if p != "oracle" else actual
                if not all(np.isfinite(req)):
                    continue
                req = [float(x) for x in req]
                if p == "myopic":
                    req_use = [req[0]]
                else:
                    req_use = req
                best = plan(req_use, caps, con, model, cur_on, st[p].last_switch_at, ts, power_cap)
                if best is None:
                    continue
                combo = best["seq"][0]
                alloc0 = best["alloc0"]
                acts = _actions(combo, alloc0, caps, cur_on, cur_flows)
                alloc_power = alloc0["power_kw"]
                flows = alloc0["flows"]
                trans = CONS.transition_feasibility(cur_on, combo, caps, req_use[0], con.reserve)[1]
                status = "success"
                row[f"{p}_hplan_supply"] = best["horizon_plan_supply_m3"]
                row[f"{p}_switches"] = best["switches"]
                row[f"{p}_seq"] = "->".join("".join(c) if c else "off" for c in best["seq"])

            surge_level, blocked, _ = _surge(snap, acts, trans)
            trans_ok = all(s.get("supply_ok", True) for s in trans)
            exec_ok = (not blocked) and trans_ok
            row[f"{p}_status"] = status
            row[f"{p}_flow"] = sum(flows.values())
            row[f"{p}_power"] = alloc_power
            row[f"{p}_f4"] = float(flows.get("4#", 0.0))
            row[f"{p}_f5"] = float(flows.get("5#", 0.0))
            row[f"{p}_n"] = len(combo)
            row[f"{p}_surge"], row[f"{p}_blocked"], row[f"{p}_exec"] = surge_level, blocked, exec_ok
            # 服务等价标记: 是否满足预测要求 / 是否满足实测需求
            supply = row[f"{p}_flow"]
            # 容差 0.05 m3/min 吸收供气量四舍五入到 2 位小数的误差
            row[f"{p}_met_forecast"] = (exec_ok and row["required_forecast"] is not None
                                        and supply is not None and supply + 0.05 >= row["required_forecast"])
            row[f"{p}_met_actual"] = (exec_ok and np.isfinite(obs_flow)
                                      and supply is not None and supply + 0.05 >= obs_flow)
            if exec_ok:
                _apply(sim[p], acts, ts, st[p])
        rows.append(row)
    return pd.DataFrame(rows), con, STEP


# ------------------------------------------------------------------ #
# 指标
# ------------------------------------------------------------------ #
def _big_small_minutes(g, p, step, rated=268.9, ratio=0.5):
    """逐机大马拉小车分钟: 任一机组运行且 load_ratio = flow/rated < ratio。"""
    mins = 0.0
    for d in ("4#", "5#"):
        col = f"{p}_f{d[0]}"
        if col in g:
            mins += float(((g[col] > 0) & (g[col] / rated < ratio)).sum()) * step
    return round(mins, 1)


def _metrics(df, con, step):
    dt_h = step / 60.0
    out = {"protocol": {
        "window": f"{VAL_START}~{VAL_END}", "step_minutes": step, "horizon_steps": HORIZON,
        "horizon_minutes": step * HORIZON, "reserve": "frozen extra 5%",
        "demand_basis": con.demand_basis, "uses_B5_final_window": False,
        "switch_penalty_kwh_per_event": SWITCH_PENALTY_KWH,
        "evidence_scope": "开发/验证窗口 + 仅过去信息 (季节日) 预测; oracle 仅诊断; 非 A 冻结口径, 非达标证据"}}
    per = {}
    for p in ("b1_rule", "myopic", "multi_step", "oracle"):
        g = df[df[f"{p}_status"] == "success"].copy()
        g = g[g[f"{p}_exec"]]
        n = int(len(g))
        E = float(g[f"{p}_power"].sum()) * dt_h if n else 0.0
        V = float(g[f"{p}_flow"].sum()) * step if n else 0.0
        short = float((g["obs_flow"] - g[f"{p}_flow"]).clip(lower=0).sum()) * step if n else 0.0
        per[p] = {"evaluable_steps": n, "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "sec_kwh_per_m3": round(E / V, 6) if V else None,
                  "shortfall_vs_observed_m3": round(short, 2),
                  "surge": dict(Counter(df.loc[df[f"{p}_status"] == "success", f"{p}_surge"])),
                  "blocked_steps": int(((df[f"{p}_status"] == "success") & df[f"{p}_blocked"]).sum()),
                  "big_small_minutes": _big_small_minutes(g, p, step)}
    # 公平比较: 四种规划器均可执行步
    ok = df[[f"{p}_status" for p in per]].eq("success").all(axis=1)
    for p in per:
        ok = ok & df[f"{p}_exec"]
    fair = df[ok]
    out["per_planner"] = per
    out["fair_steps"] = int(len(fair))
    if len(fair):
        E = {p: float(fair[f"{p}_power"].sum()) * dt_h for p in per}
        V = {p: float(fair[f"{p}_flow"].sum()) * step for p in per}
        out["fair_energy_kwh"] = {p: round(E[p], 2) for p in E}
        out["fair_supply_m3"] = {p: round(V[p], 1) for p in V}
        out["fair_sec"] = {p: round(E[p] / V[p], 6) if V[p] else None for p in per}
        out["fair_shortfall_vs_observed_m3"] = {
            p: round(float((fair["obs_flow"] - fair[f"{p}_flow"]).clip(lower=0).sum()) * step, 2) for p in per}
        out["fair_big_small_minutes"] = {p: _big_small_minutes(fair, p, step) for p in per}
        _b1bs = out["fair_big_small_minutes"]["b1_rule"]
        out["fair_big_small_reduction_pct"] = {
            p: (round((_b1bs - out["fair_big_small_minutes"][p]) / _b1bs * 100, 4) if _b1bs > 0 else None)
            for p in ("myopic", "multi_step")}
        b1 = E["b1_rule"]
        for p in ("myopic", "multi_step", "oracle"):
            out[f"{p}_vs_b1_saving_pct"] = round((b1 - E[p]) / b1 * 100, 4) if b1 else None
        myo = E["myopic"]
        out["multi_step_vs_myopic_pct"] = round((myo - E["multi_step"]) / myo * 100, 4) if myo else None
        out["oracle_vs_myopic_pct"] = round((myo - E["oracle"]) / myo * 100, 4) if myo else None
        # 可执行覆盖: myopic 因锁定不可行而缺步, multi_step 用未来步吸收切换
        out["coverage"] = {p: int((df[f"{p}_status"] == "success").sum()) for p in per}
        out["myopic_infeasible_steps"] = int((df["myopic_status"] != "success").sum())
        out["note_pct_basis"] = ("*_vs_b1_saving_pct 为相对 B1 的电量降幅(%); "
                                 "multi_step_vs_myopic_pct 为相对 myopic(同规划器 H=1) 的降幅(%); "
                                 "两者口径不同, 不可混加")

    # ---- 时间对齐与缺供归因 ----
    fin = df[np.isfinite(df["required_forecast"])].copy()
    fin["fc_err"] = fin["required_forecast"] - fin["obs_flow"]  # >0 高估, <0 低估
    out["alignment"] = {
        "required_forecast_mean_m3min": round(float(fin["required_forecast"].mean()), 3),
        "required_actual_mean_m3min": round(float(fin["required_actual"].mean()), 3),
        "forecast_error_mean_m3min": round(float(fin["fc_err"].mean()), 3),
        "forecast_underestimate_share": round(float((fin["fc_err"] < 0).mean()), 4),
        "note": ("预测在 t 发出, 目标供气区间 [t,t+15min); required_forecast=前一日同时刻流量, "
                 "required_actual=obs_flow(t). 缺供主要由预测低估(前一日同时刻→当日)造成, 而非算法不满足其预测要求.")}

    # ---- 服务等价子集: 各规划器均满足**实测需求**且成功可执行 ----
    svc = df[[f"{p}_status" for p in ("b1_rule", "myopic", "multi_step")]].eq("success").all(axis=1)
    for p in ("b1_rule", "myopic", "multi_step"):
        svc = svc & df[f"{p}_exec"] & df[f"{p}_met_actual"]
    s = df[svc]
    out["service_matched_steps"] = int(len(s))
    if len(s):
        E = {p: float(s[f"{p}_power"].sum()) * dt_h for p in ("b1_rule", "myopic", "multi_step")}
        V = {p: float(s[f"{p}_flow"].sum()) * step for p in ("b1_rule", "myopic", "multi_step")}
        out["service_matched_energy_kwh"] = {p: round(E[p], 2) for p in E}
        out["service_matched_sec"] = {p: round(E[p] / V[p], 6) if V[p] else None for p in E}
        b1 = E["b1_rule"]
        for p in ("myopic", "multi_step"):
            out[f"service_matched_{p}_vs_b1_pct"] = round((b1 - E[p]) / b1 * 100, 4) if b1 else None
        out["service_matched_big_small_minutes"] = {
            p: _big_small_minutes(s, p, step) for p in ("b1_rule", "myopic", "multi_step")}
        out["service_matched_fair_steps"] = int(len(s))
    return out


def main():
    t0 = time.perf_counter()
    df, con, step = replay()
    df.to_csv(LED, index=False)
    rep = _metrics(df, con, step)
    rep["n_steps"] = int(len(df))
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)

    # 多步相对单步新增的可执行步轨迹 (myopic 不可行/未成功, 但 multi_step 成功)
    extra = df[(df["multi_step_status"] == "success") & (df["myopic_status"] != "success")].copy()
    cols = ["t", "obs_flow", "required_forecast", "myopic_status", "multi_step_cur_on",
            "multi_step_n", "multi_step_flow", "multi_step_seq", "multi_step_surge",
            "multi_step_blocked", "multi_step_met_forecast", "multi_step_met_actual"]
    cols = [c for c in cols if c in extra.columns]
    extra[cols].to_csv(TRACE, index=False)
    rep["myopic_infeasible_covered_by_multistep"] = int(len(extra))
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
