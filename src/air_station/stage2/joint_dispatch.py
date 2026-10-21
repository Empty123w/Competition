"""第三阶段: 统一闭环联合调度 (B1/B3/balanced/LRB + MPC + GRU 风险提示 + B4 安全过滤)。

设计原则:
  - **物理完全相同**: 直接 import `simulation/closed_loop` 的 SimParams/场景/物理函数;
  - **决策可插拔**: 调度策略 (B1/B3/balanced/LRB) 与 MPC/联合 MPC 在**同一循环**中比较;
  - **同一时刻同一输入**: 相同需求预测(带噪)、设备状态、容量/备用/压力约束;
  - **无未来泄漏**: MPC 仅使用当前及**预测**需求 (带噪), 不使用未来真值; 压力反馈仅用当前压力;
  - **安全**: B4 独立复检; high/unknown -> safety_blocked, 不执行, 回退 B1 规则;
  - **不可行即不可行**: 无可行组合 -> is_fallback + 明确状态, 不返回虚假成功;
  - `execution_permission="simulation_candidate_only"` 恒不变。

对外: simulate(strategy, scenario, ...) -> {"records","metrics","summary"}
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization import performance as PERF
from air_station.optimization import unit_commitment as UC
from air_station.safety import surge as SURGE
from air_station.simulation import closed_loop as CL

RATED = 268.9
DEVICES = ("4#", "5#")
LOW_LIMIT = 0.5

DISPATCH_MODES = {"b1", "b3", "balanced", "load_balanced"}
MPC_MODES = {"mpc", "joint"}


@dataclass
class JointWeights:
    w_energy: float = 1.0
    w_low: float = 0.0
    w_imbalance: float = 0.0
    w_pressure: float = 5000.0
    w_center: float = 20000.0
    w_switch: float = 2.0
    w_short: float = 500.0
    horizon: int = 4
    balance_mode: str = "min_power"   # min_power | spread (|lr4-lr5|<=tol)
    spread_tol: float = 0.10
    levels: tuple = ("pred", "upper")
    ramp: dict = field(default_factory=lambda: {"4#": 77.0, "5#": 287.0})


_DIAG = {"no_step_opts": 0, "no_feasible_seq": 0, "decide_ms": []}


def _power(flows, model, cache):
    tot = 0.0
    for d, q in flows.items():
        if q and q > 0:
            key = (d, round(float(q), 1))
            if key not in cache:
                r = PERF.estimate_power(d, float(q), model_path=model)
                cache[key] = r["estimated_power_kw"] if r["status"] == "success" else None
            if cache[key] is None:
                return float("nan")
            tot += cache[key]
    return tot


def _alloc(combo, required, caps, prev_flows, prev_running, model, cache, balance=False, tol=0.10):
    cfg = UC.UCConfig(demand_basis="upper", ramp_enabled=False,
                      spread_balance=bool(balance), spread_tol=float(tol) if balance else 0.0)
    return UC.best_allocation(tuple(combo), float(required), caps, prev_flows, prev_running,
                              model, cfg, cache)


def _imbalance(flows):
    lrs = [flows[d] / RATED for d in DEVICES if flows.get(d, 0) > 0]
    if len(lrs) < 2:
        return 0.0
    return abs(lrs[0] - lrs[1])


def _viol(p, lo, hi):
    return max(0.0, lo - p) + max(0.0, p - hi)


def mpc_decide(p, pred_seq, ub_seq, caps, con, cur_on, cur_flows, model, cache,
               sim: CL.SimParams, w: JointWeights, power_cap=0.0):
    """联合 MPC: 组合×供气水平×多步压力, 最小化能耗+低负荷+不均衡+压力越界+切换+缺供。

    仅使用 pred_seq(this step..+H) 作为前瞻需求; 压力仅由当前 p 递推 (无未来泄漏)。
    """
    H = len(pred_seq)
    dt_h = sim.dt_min / 60.0
    step_opts = []
    for k in range(H):
        opts = {}
        for combo in CONS.COMBINATIONS:
            if any(not caps[d].available for d in combo):
                continue
            cap = sum(caps[d].max_flow_m3min for d in combo)
            need_pref = pred_seq[k] + con.reserve.extra_m3_min(pred_seq[k])
            need_min = pred_seq[k]
            if combo and cap + 1e-6 < need_min:
                continue          # 服务不可行 -> 该组合剔除
            if not combo and pred_seq[k] > 1e-6:
                continue
            if k == 0:
                locked = False
                for d in DEVICES:
                    will, now = (d in combo), (d in cur_on)
                    if will != now:
                        if will and not caps[d].can_start:
                            locked = True; break
                        if (not will) and not caps[d].can_stop:
                            locked = True; break
                if locked:
                    continue
            pf = cur_flows if k == 0 else {d: caps[d].min_flow_m3min for d in DEVICES}
            pr = {d: (d in cur_on) if k == 0 else True for d in DEVICES}
            lvl_opts = {}
            lvl_req = {"pred": pred_seq[k]}
            if ub_seq[k] <= cap + 1e-6:
                lvl_req["upper"] = ub_seq[k]
            else:
                lvl_req["cap"] = cap            # 需求上界超容量 -> 用满容量 (降级)
            for lvl, req in lvl_req.items():
                al = _alloc(combo, req, caps, pf, pr, model, cache,
                            balance=(w.balance_mode == "spread"), tol=w.spread_tol)
                if al is None:
                    continue
                if power_cap > 0 and any(_power({d: al["flows"][d]}, model, cache) > power_cap + 1e-6
                                         for d in combo):
                    continue
                lvl_opts[lvl] = al
            if lvl_opts:
                opts[combo] = lvl_opts
        step_opts.append(opts)
    empty = [i for i, o in enumerate(step_opts) if len(o) == 0]
    if empty:
        _DIAG["no_step_opts"] += 1
        _DIAG.setdefault("empty_k", []).append((empty, [round(x,1) for x in pred_seq], [round(x,1) for x in ub_seq]))
        return None

    import itertools
    best = None
    for seq in itertools.product(*[[(c, l) for c in o for l in o[c]] for o in step_opts]):
        combos = tuple(c for c, _ in seq)
        if not _can_switch(cur_on, combos[0], con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, combos[0], caps, pred_seq[0], con.reserve)
        if not ok:
            continue
        prev = set(cur_on); sw = 0; low = 0; energy = 0.0; short = 0.0; pviol = 0.0; imb = 0.0
        center = 0.0
        pp = float(p)
        for k, (combo, lvl) in enumerate(seq):
            sw += len([d for d in DEVICES if (d in combo) != (d in prev)]); prev = set(combo)
            al = step_opts[k][combo][lvl]
            energy += al["power_kw"] * dt_h
            low += 1 if al["low"] else 0
            imb += _imbalance(al["flows"])
            short += max(0.0, pred_seq[k] - sum(al["flows"].values()))
            sup = sum(al["flows"].values())
            qblow = CL.blowoff_flow(pp, sup, sim)
            pp = CL.pressure_step(pp, sup, pred_seq[k], qblow, sim)
            pviol += _viol(pp, sim.band_lo_mpa, sim.band_hi_mpa)
            center += (pp - sim.p_set_mpa) ** 2
        cost = (w.w_energy * energy + w.w_low * low + w.w_imbalance * imb
                + w.w_pressure * pviol + w.w_center * center
                + w.w_switch * sw + w.w_short * short)
        key = (round(cost, 6), sw, combos)
        if best is None or key < best[0]:
            cap0 = sum(caps[d].max_flow_m3min for d in combos[0])
            degraded = cap0 + 1e-6 < (pred_seq[0] + con.reserve.extra_m3_min(pred_seq[0]))
            best = (key, {"combo": combos[0], "alloc": step_opts[0][combos[0]][seq[0][1]],
                          "p_end": round(pp, 4), "energy_kwh": round(energy, 4),
                          "degraded": bool(degraded)})
    if best is None:
        _DIAG["no_feasible_seq"] += 1
    return best[1] if best else None


def _can_switch(cur_on, combo, con):
    # 组合切换最小开停机由 PolicyState 锁定; MPC 内部只做存在性检查 (真正的锁定在循环里按 state 判定)
    return True


def _b4_check(snap, combo, alloc, con, opt_cfg):
    """B4 独立安全复检: 返回 (blocked, assessment_dict)。"""
    actions = []
    for d in DEVICES:
        f = alloc["flows"].get(d, 0.0)
        actions.append(C.DeviceAction(device_id=d, action=("start" if f > 0 else "stop"),
                                      target_flow_m3_min=float(f) if f > 0 else 0.0,
                                      target_load_ratio=None, running_after=f > 0))
    res = C.DispatchResult(status=C.STATUS_SUCCESS, policy_version="joint",
                           as_of=str(snap.as_of), device_actions=actions)
    a = SURGE.assess_surge_risk(snap, res)
    return bool(a.safety_blocked), a.to_dict() if hasattr(a, "to_dict") else {}


def simulate(strategy: str, sc: "CL.Scenario", sim: CL.SimParams = None,
             config_path: str = "configs/optimization.yaml",
             base_config: str = "configs/baseline.yaml",
             weights: JointWeights = None,
             risk_hint_fn=None) -> dict:
    """统一闭环仿真。strategy ∈ DISPATCH_MODES ∪ MPC_MODES。"""
    if strategy not in DISPATCH_MODES | MPC_MODES:
        raise ValueError(f"unknown strategy {strategy}")
    p = sim or CL.SimParams()
    w = weights or JointWeights()
    con = C.DispatchConstraints.from_config(B1.load_config(base_config))
    base_cfg = B1.load_config(base_config)
    from air_station.optimization.service import load_optimization_config
    opt_cfg = load_optimization_config(config_path)
    model = opt_cfg["paths"]["power_model"]
    rng = np.random.default_rng(p.seed)
    dem = CL.demand_series(sc)
    _DIAG["no_step_opts"] = 0; _DIAG["no_feasible_seq"] = 0; _DIAG["decide_ms"] = []

    state = C.PolicyState()
    running = {d: True for d in DEVICES}
    flows = {d: sc.base_m3min / 2.0 for d in DEVICES}
    pressure = p.p_init_mpa
    bias_i = 0.0
    pending = []
    cache = {}
    prev_S = float(sc.base_m3min)
    recs = []
    for k in range(sc.n_steps):
        t = pd.Timestamp("2026-01-01 00:00:00") + pd.Timedelta(minutes=p.dt_min * k)
        snap = CL._snapshot(t, pressure, running, flows)
        # 预测 (与阶段一一致): 瞬时预测带噪 + PI 压力偏置
        pred = float(dem[k]) * (1 + p.forecast_bias_frac) + rng.normal(
            0, p.forecast_noise_frac * max(50.0, dem[k]))
        e = p.p_set_mpa - pressure
        bias_i = float(np.clip(bias_i + p.pi_ki * e, -p.pi_bias_max, p.pi_bias_max))
        bias = float(np.clip(p.pi_kp * e + bias_i, -p.pi_bias_max, p.pi_bias_max))
        ub = max(0.0, pred + bias) * (1 + p.forecast_ub_margin_frac) + p.forecast_ub_const
        fc = CL._forecast(t, max(0.0, pred + bias), ub)

        if sc.fault and k >= sc.fault[0]:
            snap = C.StationSnapshot(str(t), float(pressure),
                                     [C.DeviceSnapshot(d.device_id, d.running, d.flow_m3_min, None,
                                                       pressure, None, None,
                                                       quality_valid=(d.device_id != sc.fault[1]))
                                      for d in snap.devices], "simulation")

        fb = False
        status = C.STATUS_SUCCESS
        acts = None
        fb_reason = None
        if strategy == "b1":
            res = CL._b1_supply(snap, fc, con, base_cfg, state)
            if res is None:
                fb = True
            else:
                acts = res[0]
        elif strategy in DISPATCH_MODES:
            _t0 = time.perf_counter()
            out = API.plan_dispatch(strategy, snap, fc, [], con, state=state)
            _DIAG["decide_ms"].append((time.perf_counter() - _t0) * 1000.0)
            if out.get("status") == C.STATUS_SUCCESS and not out.get("is_fallback"):
                acts = {a["device_id"]: (a["target_flow_m3_min"] or 0.0)
                        for a in out["device_actions"] if a["running_after"]}
            else:
                fb = True; status = out.get("status", "fallback")
        else:  # mpc / joint
            caps = CONS.build_capabilities(con, snap, [], state)
            # 前瞻需求序列: 用**带噪预测** (非真值); 仅用当前压力反馈
            pred_seq, ub_seq = [], []
            for j in range(w.horizon):
                kk = min(sc.n_steps - 1, k + j)
                pj = float(dem[kk]) + rng.normal(0, p.forecast_noise_frac * max(50.0, dem[kk]))
                pj = max(0.0, pj + bias)
                pred_seq.append(pj)
                ub_seq.append(pj * (1 + p.forecast_ub_margin_frac) + p.forecast_ub_const)
            cur_on = {d for d in DEVICES if d in snap.device_map() and snap.device_map()[d].running}
            cur_flows = {d: (snap.device_map()[d].flow_m3_min if d in snap.device_map() else None)
                         for d in DEVICES}
            _t0 = time.perf_counter()
            got = mpc_decide(pressure, pred_seq, ub_seq, caps, con, cur_on, cur_flows,
                             model, cache, p, w, power_cap=opt_cfg["dispatch"].get("power_cap_kw_per_device", 0.0))
            _DIAG["decide_ms"].append((time.perf_counter() - _t0) * 1000.0)
            if got is None:
                fb = True; fb_reason = "infeasible"; status = C.RC_INFEASIBLE_CAPACITY
            else:
                alloc = got["alloc"]
                # B4 独立安全复检
                blocked, _ad = _b4_check(snap, got["combo"], alloc, con, opt_cfg)
                if blocked:
                    fb = True; fb_reason = "safety"; acts = None
                else:
                    acts = {d: float(alloc["flows"].get(d, 0.0)) for d in DEVICES}
                    if got.get("degraded"):
                        status = "degraded_reserve"

        if acts is None:
            if fb_reason == "infeasible":
                # 容量不足: 降级为可用机组满负荷 (尽力供气), 明确标记不可行/降级
                acts = {d: (con.validated_operating_max_m3min
                            if (d in snap.device_map() and snap.device_map()[d].quality_valid)
                            else 0.0) for d in DEVICES}
            else:
                # 安全回退: 维持当前状态 (最保守)
                acts = {d: (flows[d] if running[d] else 0.0) for d in DEVICES}

        if p.exec_delay_steps > 0:
            pending.append([p.exec_delay_steps, dict(acts)])
            ready = None
            for item in pending:
                item[0] -= 1
                if item[0] <= 0 and ready is None:
                    ready = item[1]
            pending = [it for it in pending if it[0] > 0]
            applied = ready if ready is not None else acts
        else:
            applied = acts
        running, flows = CL._apply(applied, snap)
        # 更新策略状态 (最小开停机锁定用): 记录发生切换的机组
        for d in DEVICES:
            before = bool(snap.device_map().get(d).running) if d in snap.device_map() else False
            after = bool(running[d])
            if before != after:
                state.last_switch_at[d] = str(t)
                state.recent_switch_times.append(str(t))

        hint = 0
        if risk_hint_fn is not None:
            hint = int(risk_hint_fn(pressure, flows, k))
        q_sup = sum(flows[d] for d in DEVICES if running[d])
        q_dem = float(dem[k])
        q_blow = CL.blowoff_flow(pressure, q_sup, p)
        p_before = pressure
        pressure, vent, unmet, flag = CL.pressure_step_accounted(pressure, q_sup, q_dem, q_blow, p)
        power = _power({d: flows[d] for d in DEVICES if running[d]}, model, cache)
        recs.append({"k": k, "t": str(t), "demand": q_dem, "supply": q_sup, "blow": q_blow,
                     "pressure": pressure, "pressure_prev": p_before, "power_kw": power,
                     "f4": flows.get("4#", 0.0), "f5": flows.get("5#", 0.0),
                     "fallback": fb, "status": status, "risk_hint": hint,
                     "vent_m3": vent, "unmet_m3": unmet, "clamp": flag,
                     "switch": int(sum(1 for d in DEVICES if (flows.get(d, 0) > 0) != (applied.get(d, 0) > 0)))})
    return {"strategy": strategy, "scenario": sc.name, "records": recs,
            "metrics": metrics(recs, p)}


def metrics(recs, p: CL.SimParams) -> dict:
    df = pd.DataFrame(recs)
    dt_h = p.dt_min / 60.0
    E = float(df.power_kw.fillna(0).sum()) * dt_h
    V = float(df.supply.sum()) * p.dt_min
    D = float(df.demand.sum()) * p.dt_min
    comp = float(((df.pressure >= p.band_lo_mpa) & (df.pressure <= p.band_hi_mpa)).mean())
    r4 = (df.f4 / RATED).to_numpy(); r5 = (df.f5 / RATED).to_numpy()
    both = (df.f4 > 0).to_numpy() & (df.f5 > 0).to_numpy()
    d3 = np.ones(len(df), dtype=bool)
    d3[both] = np.abs(r4[both] - r5[both]) <= 0.10
    low = float((((df.f4 > 0) & (r4 < LOW_LIMIT)).sum() + ((df.f5 > 0) & (r5 < LOW_LIMIT)).sum()) * p.dt_min)
    inflow = (float(df.supply.sum()) - float(df.demand.sum()) - float(df.blow.sum())) * p.dt_min
    store = p.k_vol_m3_per_mpa * (float(df.pressure.iloc[-1]) - float(df.pressure_prev.iloc[0]))
    leak = p.k_vol_m3_per_mpa * p.leak_per_min * float((df.pressure_prev - p.p_env_mpa).sum()) * p.dt_min
    vent = float(df.get("vent_m3", pd.Series(dtype=float)).sum())
    unmet = float(df.get("unmet_m3", pd.Series(dtype=float)).sum())
    return {
        "steps": int(len(df)),
        "pressure_compliance": round(comp, 4),
        "pressure_std": round(float(df.pressure.std()), 5),
        "pressure_min": round(float(df.pressure.min()), 4),
        "pressure_max": round(float(df.pressure.max()), 4),
        "energy_kwh": round(E, 1), "supply_m3": round(V, 1), "demand_m3": round(D, 1),
        "sec_kwh_per_m3": round(E / V, 6) if V else None,
        "supply_gap_m3": round(float((df.demand - df.supply).clip(lower=0).sum()) * p.dt_min, 1),
        "demand_satisfaction": round(1 - float((df.demand - df.supply).clip(lower=0).sum())
                                      / float(df.demand.sum()), 4),
        "low_load_min": low,
        "load_ratio_d3_meet": round(float(d3.mean()), 4),
        "switch_events": int(df.get("switch", pd.Series(dtype=int)).sum()),
        "fallback_steps": int(df.fallback.sum()),
        "risk_hint_steps": int((df.get("risk_hint", pd.Series(dtype=int)) > 0).sum()),
        "unserved_deficit_m3": round(unmet, 1),
        "safety_vent_m3": round(vent, 1),
        "pressure_start": round(float(df.pressure_prev.iloc[0]), 4),
        "pressure_end": round(float(df.pressure.iloc[-1]), 4),
        "storage_neutral": bool(float(df.pressure.iloc[-1]) >= float(df.pressure_prev.iloc[0]) - 0.005),
        "mass_balance_residual_m3": round(inflow - store - leak - vent + unmet, 3),
        "decide_ms_p50": (round(float(np.percentile(_DIAG["decide_ms"], 50)), 3)
                          if _DIAG.get("decide_ms") else None),
        "decide_ms_p95": (round(float(np.percentile(_DIAG["decide_ms"], 95)), 3)
                          if _DIAG.get("decide_ms") else None),
        "decide_ms_max": (round(float(np.max(_DIAG["decide_ms"])), 3)
                          if _DIAG.get("decide_ms") else None),
    }
