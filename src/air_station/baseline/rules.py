"""传统人工规则调度基线 (阶段 B1)。

设计原则 (任务书 V1.1 §5):
  - 纯函数: 只依赖传入对象与配置, 无 I/O、无全局可变状态;
  - 运行机组优先保持, 压力越界经连续确认后动作, 固定启停优先级;
  - 最小运行/停机锁定, 动作迟滞, 预测缺失时守恒;
  - 需求与备用为硬约束, 缺数据/不可行返回结构化状态;
  - 所有阈值来自 configs/baseline.yaml, 代码不硬编码。

重要: 本模块是**对照基线**, 不是逐条复制历史开停机动作, 不窥视未来真实流量。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd

from air_station.optimization import contracts as C
from air_station.optimization.contracts import (
    ConstraintCheck,
    DeviceAction,
    DispatchConstraints,
    DispatchResult,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    StationSnapshot,
    safe_float,
)

DEFAULT_CONFIG = "configs/baseline.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[baseline] 配置文件缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ #
# 小工具
# ------------------------------------------------------------------ #
def _ts(value) -> pd.Timestamp:
    return pd.Timestamp(value)


def _clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo:
        return lo
    return max(lo, min(hi, v))


def _forecast_status(snapshot: StationSnapshot, forecast: Optional[ForecastResult],
                     constraints: DispatchConstraints) -> str:
    """ok | missing | stale。"""
    if forecast is None:
        return "missing"
    fs, ss = _ts(forecast.as_of), _ts(snapshot.as_of)
    if fs > ss:
        return "stale"                       # 未来预测相对快照非法
    age_min = (ss - fs).total_seconds() / 60.0
    if age_min > constraints.max_forecast_age_minutes:
        return "stale"
    return "ok"


def _pressure_sustained(state: PolicyState, lo: float, hi: float, confirm: int):
    """返回 (low_sustained, high_sustained, debounce_status)。"""
    hist = state.pressure_history or []
    if len(hist) < confirm:
        return False, False, "insufficient"
    last = [float(v) for _, v in hist[-confirm:]]
    return all(v < lo for v in last), all(v > hi for v in last), "ok"


def _lock_capability(device_id: str, running: bool, as_of: pd.Timestamp,
                     state: PolicyState, constraints: DispatchConstraints):
    """返回 (can_start, can_stop): 最小运行/停机锁定。"""
    last = state.last_switch_at.get(device_id)
    if last is None:
        return (not running), running
    delta_min = (as_of - _ts(last)).total_seconds() / 60.0
    if running:
        return False, delta_min >= constraints.min_run_minutes
    return delta_min >= constraints.min_stop_minutes, False


def _switch_budget_ok(state: PolicyState, as_of: pd.Timestamp,
                      constraints: DispatchConstraints) -> bool:
    recent = [t for t in state.recent_switch_times
              if (as_of - _ts(t)).total_seconds() / 60.0 <= 60.0]
    return len(recent) < constraints.max_switches_per_hour


def _health_factor(device_id: str, health_map: dict, constraints: DispatchConstraints):
    """返回 (factor, internal_risk)。observe 模式下 factor 恒为 1.0。"""
    h = health_map.get(device_id)
    if h is None:
        return 1.0, "normal"
    risk = h.internal_risk
    if constraints.health_mode != "derate":
        return 1.0, risk
    return float(constraints.health_derate_map.get(risk, 1.0)), risk


def _effective_max(device_id: str, constraints: DispatchConstraints, health_map: dict) -> float:
    factor, _ = _health_factor(device_id, health_map, constraints)
    return float(constraints.validated_operating_max_m3min) * factor


def _capacity(running: dict, eff_max: dict) -> float:
    return float(sum(eff_max[d] for d in C.DEVICE_IDS if running.get(d)))


def _allocate(on: list[str], required: float, eff_max: dict, lo: float,
              constraints: DispatchConstraints, cfg: dict) -> dict:
    """在运行机组间分配需求流量, 返回 {device: flow}。

    基载机 (默认 4#) 优先到高加载档, 调节机 (默认 5#) 吸收剩余量。
    """
    flows: dict[str, float] = {}
    if not on:
        return flows
    mins = {d: float(lo) for d in on}
    maxs = {d: float(eff_max[d]) for d in on}
    if len(on) == 1:
        d = on[0]
        flows[d] = _clamp(required, mins[d], maxs[d])
        return flows

    alloc = cfg.get("priority", {}).get("load_allocation", {})
    base = C.normalize_device_id(alloc.get("base_device", "4#"))
    trim = C.normalize_device_id(alloc.get("trim_device", "5#"))
    if base in on and trim in on:
        base_flow = _clamp(required - mins[trim], mins[base], maxs[base])
        trim_flow = required - base_flow
        if trim_flow > maxs[trim]:
            trim_flow = maxs[trim]
            base_flow = _clamp(required - trim_flow, mins[base], maxs[base])
        if trim_flow < mins[trim]:
            trim_flow = mins[trim]
            base_flow = _clamp(required - trim_flow, mins[base], maxs[base])
        flows[base] = base_flow
        flows[trim] = trim_flow
        return flows

    # 通用回退: 按容量比例分配并夹逼
    total_cap = sum(maxs.values())
    for d in on:
        share = maxs[d] / total_cap if total_cap > 0 else 1.0 / len(on)
        flows[d] = _clamp(required * share, mins[d], maxs[d])
    return flows


def _estimate_power(flow: Optional[float], cfg: dict) -> Optional[float]:
    pe = cfg.get("power_estimate", {})
    if pe.get("mode") == "constant" and flow is not None:
        return round(float(flow) * float(pe.get("constant_specific_power_kw_per_m3min", 5.3)), 2)
    return None


def _base_result(snapshot, cfg, constraints) -> DispatchResult:
    return DispatchResult(
        status=C.STATUS_SUCCESS,
        as_of=_ts(snapshot.as_of).isoformat(),
        policy_version=cfg.get("meta", {}).get("policy_version", "baseline-rule-v0.1"),
        validation_mode=constraints.applied_validation_mode(),
        source_flags=dict(constraints.source_flags),
        pressure_setpoint_mpa=constraints.pressure_setpoint_mpa,
    )


def _finalize(result: DispatchResult, t0: float) -> DispatchResult:
    result.elapsed_seconds = round(time.perf_counter() - t0, 4)
    return result


# ------------------------------------------------------------------ #
# 主决策入口
# ------------------------------------------------------------------ #
def decide(
    snapshot: StationSnapshot,
    forecast: Optional[ForecastResult],
    health: list[HealthAssessment],
    constraints: DispatchConstraints,
    config: Optional[dict] = None,
    state: Optional[PolicyState] = None,
) -> DispatchResult:
    """传统人工规则调度单步决策。

    返回 DispatchResult:
      - 可行/保持 -> status=success;
      - 需求超过可用容量等 -> status=INFEASIBLE;
      - 关键输入缺失/预测过期 -> status=INSUFFICIENT_DATA / INVALID_INPUT。
    """
    t0 = time.perf_counter()
    cfg = config or {}
    state = state or PolicyState()
    result = _base_result(snapshot, cfg, constraints)

    # 1) 设备合法性 (真实设备仅 4#、5#)
    dmap = snapshot.device_map()
    ids = set(dmap.keys())
    invalid = sorted(ids - set(C.DEVICE_IDS))
    missing_dev = [d for d in C.DEVICE_IDS if d not in dmap]
    if invalid or missing_dev:
        result.status = C.STATUS_INVALID_INPUT
        result.reason_codes = [C.RC_INVALID_INPUT]
        result.explanation = (f"非法或缺失设备: 非法={invalid}, 缺失={missing_dev}; "
                              f"真实设备仅 {C.DEVICE_IDS}")
        return _finalize(result, t0)

    # 2) 压力可用性
    pressure = safe_float(snapshot.header_pressure_mpa)
    if pressure is None:
        result.status = C.STATUS_INSUFFICIENT_DATA
        result.reason_codes = [C.RC_INSUFFICIENT_DATA]
        result.explanation = "母管压力缺失, 无法判定压力越界, 拒绝调度"
        return _finalize(result, t0)

    health_map = {h.device_id: h for h in health or []}
    result.health_ids = [h.device_id for h in (health or [])]

    # 3) 预测状态
    fc_status = _forecast_status(snapshot, forecast, constraints)
    if fc_status == "stale":
        result.status = C.STATUS_INSUFFICIENT_DATA
        result.reason_codes = [C.RC_STALE_FORECAST]
        result.explanation = (f"预测过期或时间晚于快照: forecast.as_of={forecast.as_of}, "
                              f"snapshot.as_of={snapshot.as_of}")
        return _finalize(result, t0)

    as_of = _ts(snapshot.as_of)

    # 4) 健康因子与有效容量
    eff_max, health_factor, health_risk = {}, {}, {}
    for d in C.DEVICE_IDS:
        factor, risk = _health_factor(d, health_map, constraints)
        health_factor[d] = factor
        health_risk[d] = risk
        eff_max[d] = float(constraints.validated_operating_max_m3min) * factor

    # 5) 预测缺失 -> 守恒保持
    if fc_status == "missing":
        if not cfg.get("demand", {}).get("hold_when_forecast_missing", True):
            result.status = C.STATUS_INSUFFICIENT_DATA
            result.reason_codes = [C.RC_FORECAST_MISSING_HOLD]
            result.explanation = "预测缺失且配置不允许守恒, 拒绝调度"
            return _finalize(result, t0)
        return _finalize(_hold_conservative(result, snapshot, cfg, constraints,
                                            health_factor, health_risk, C.RC_FORECAST_MISSING_HOLD), t0)

    # 6) 需求与备用硬约束
    required = forecast.demand_value(constraints.demand_basis)
    if required is None:
        result.status = C.STATUS_INSUFFICIENT_DATA
        result.reason_codes = [C.RC_INSUFFICIENT_DATA]
        result.explanation = f"预测缺少 {constraints.demand_basis} 需求值"
        return _finalize(result, t0)
    result.required_flow_m3_min = required
    result.forecast_id = forecast.forecast_id

    reserve_extra = constraints.reserve.extra_m3_min(required)
    need = required + reserve_extra
    total_available = _capacity({d: True for d in C.DEVICE_IDS}, eff_max)

    if need > total_available + 1e-6:
        result.status = C.STATUS_INFEASIBLE
        result.reason_codes = [C.RC_INFEASIBLE_CAPACITY]
        result.constraints = [ConstraintCheck("capacity", False, need - total_available,
                                              "m3/min", "需求+备用超过全部可用容量")]
        result.explanation = (f"需求 {required:.1f} + 备用 {reserve_extra:.1f} = {need:.1f} "
                              f"超过可用容量 {total_available:.1f} m3/min")
        return _finalize(result, t0)

    if constraints.reserve.mode == "n_minus_one":
        largest = max(eff_max.values()) if eff_max else 0.0
        if total_available - largest < required - 1e-6:
            result.status = C.STATUS_INFEASIBLE
            result.reason_codes = [C.RC_RESERVE_VIOLATION]
            result.constraints = [ConstraintCheck("reserve_n1", False,
                                                  required - (total_available - largest),
                                                  "m3/min", "单机失效后无法满足需求")]
            result.explanation = "备用定义为 N-1, 单机失效后供气不足, 判定不可行"
            return _finalize(result, t0)

    # 7) 锁定与切换预算
    can_start, can_stop = {}, {}
    for d in C.DEVICE_IDS:
        can_start[d], can_stop[d] = _lock_capability(d, bool(dmap[d].running), as_of,
                                                     state, constraints)
    budget_ok = _switch_budget_ok(state, as_of, constraints)

    # 8) 容量驱动: 需求为硬约束, 必须满足
    running = {d: bool(dmap[d].running) for d in C.DEVICE_IDS}
    reasons: list[str] = []
    deferred_msgs: list[str] = []

    starts: list[str] = []
    while _capacity(running, eff_max) < need - 1e-6:
        cand = next((d for d in constraints.start_priority
                     if d in C.DEVICE_IDS and not running[d] and can_start[d] and eff_max[d] > 0),
                    None)
        if cand is None:
            break
        running[cand] = True
        starts.append(cand)
        reasons.append(C.RC_CAPACITY_START)

    if _capacity(running, eff_max) < need - 1e-6:
        # 可用容量足够却因锁定/延迟无法满足需求 -> 不生成未验证动作
        rc = C.RC_RESERVE_VIOLATION if total_available >= need - 1e-6 else C.RC_INFEASIBLE_CAPACITY
        result.status = C.STATUS_INFEASIBLE
        result.reason_codes = [rc]
        result.constraints = [ConstraintCheck("capacity", False,
                                              need - _capacity(running, eff_max), "m3/min",
                                              "可启动机组受最小停机锁定或容量限制")]
        result.explanation = "需求无法由可启动机组满足 (受最小停机锁定/延迟), 判定不可行"
        return _finalize(result, t0)

    # 9) 压力驱动 (每次至多切换一台, 受最小锁定与切换预算约束)
    lo, hi = constraints.pressure_band_mpa
    low_sust, high_sust, debounce = _pressure_sustained(state, lo, hi, constraints.confirm_minutes)
    hard_low = pressure < constraints.pressure_hard_lo_mpa
    pressure_outside = pressure < lo or pressure > hi
    if pressure_outside and debounce == "insufficient" and not hard_low:
        reasons.append(C.RC_DEBOUNCE_INSUFFICIENT)

    stops: list[str] = []
    if hard_low or low_sust:
        if hard_low or budget_ok:
            cand = next((d for d in constraints.start_priority
                         if d in C.DEVICE_IDS and not running[d] and can_start[d] and eff_max[d] > 0),
                        None)
            if cand is not None:
                running[cand] = True
                starts.append(cand)
                reasons.append(C.RC_PRESSURE_HARD_LOW if hard_low else C.RC_PRESSURE_LOW_START)
            elif any(not running[d] for d in C.DEVICE_IDS):
                blocked = [d for d in C.DEVICE_IDS if not running[d] and not can_start[d]]
                if blocked:
                    deferred_msgs.append(f"最小停机锁定, 延时启动: {blocked}")
                    reasons.append(C.RC_MIN_STOP_LOCK)
            else:
                reasons.append(C.RC_ADJUST_LOAD)
        else:
            reasons.append(C.RC_MAX_SWITCH_LOCK)
    elif high_sust:
        if budget_ok:
            stop_cand = None
            for d in constraints.stop_priority:
                if d in C.DEVICE_IDS and running[d] and can_stop[d] and \
                        _capacity({**running, d: False}, eff_max) >= need - 1e-6:
                    stop_cand = d
                    break
            if stop_cand is not None:
                running[stop_cand] = False
                stops.append(stop_cand)
                reasons.append(C.RC_PRESSURE_HIGH_STOP)
            else:
                locked = [d for d in C.DEVICE_IDS if running[d] and not can_stop[d]]
                if locked:
                    deferred_msgs.append(f"最小运行锁定, 延期停机: {locked}")
                    reasons.append(C.RC_MIN_RUN_LOCK)
                else:
                    reasons.append(C.RC_ADJUST_LOAD)
        else:
            reasons.append(C.RC_MAX_SWITCH_LOCK)

    # 10) 健康风险处理 (不自动停机)
    if constraints.health_mode == "derate":
        for d in C.DEVICE_IDS:
            if health_factor[d] < 1.0:
                reasons.append(C.RC_HEALTH_DERATE)
    for d in C.DEVICE_IDS:
        if health_risk.get(d) == "critical":
            reasons.append(C.RC_HEALTH_STOP_REVIEW)

    # 11) 流量分配与动作生成
    on = [d for d in C.DEVICE_IDS if running[d]]
    flows = _allocate(on, required, eff_max, constraints.validated_min_m3min, constraints, cfg)
    total_flow = float(sum(flows.values())) if flows else 0.0

    actions: list[DeviceAction] = []
    powers: list[float] = []
    power_complete = True
    for d in C.DEVICE_IDS:
        was = bool(dmap[d].running)
        now = running[d]
        rc_dev: list[str] = []
        if now:
            flow = float(flows.get(d, constraints.validated_min_m3min))
            lr_den = constraints.rated_capacity_m3min or 1.0
            lr = flow / lr_den
            power = _estimate_power(flow, cfg)
            if power is None:
                power_complete = False
            else:
                powers.append(power)
            if was:
                prev_flow = safe_float(dmap[d].flow_m3_min) or 0.0
                action = "adjust" if abs(flow - prev_flow) > 1.0 else "hold"
            else:
                action = "start"
            if constraints.health_mode == "derate" and health_factor[d] < 1.0:
                rc_dev.append(C.RC_HEALTH_DERATE)
            actions.append(DeviceAction(d, action, round(flow, 2), round(lr, 4), True,
                                        estimated_power_kw=power,
                                        reason_codes=rc_dev,
                                        upper_limit_m3_min=round(eff_max[d], 2),
                                        health_factor=round(health_factor[d], 3)))
        else:
            if was:
                action = "stop"
            else:
                action = "hold"
            actions.append(DeviceAction(d, action, 0.0, 0.0, False,
                                        estimated_power_kw=0.0,
                                        reason_codes=rc_dev,
                                        upper_limit_m3_min=round(eff_max[d], 2),
                                        health_factor=round(health_factor[d], 3)))
        if health_risk.get(d) == "critical":
            actions[-1].reason_codes.append(C.RC_HEALTH_STOP_REVIEW)

    # 12) 约束检查
    checks = [
        ConstraintCheck("demand", total_flow >= required - 1e-6, total_flow - required,
                        "m3/min", f"供气 {total_flow:.1f} vs 需求 {required:.1f}"),
        ConstraintCheck("capacity", _capacity(running, eff_max) >= need - 1e-6,
                        _capacity(running, eff_max) - need, "m3/min",
                        "计划容量 vs 需求+备用"),
        ConstraintCheck("reserve", True if constraints.reserve.mode == "none"
                        else _capacity(running, eff_max) - required >= -1e-6,
                        _capacity(running, eff_max) - required, "m3/min",
                        f"备用模式={constraints.reserve.mode}"),
        ConstraintCheck("pressure", bool(lo <= pressure <= hi),
                        min(pressure - lo, hi - pressure), "MPa",
                        f"当前压力 {pressure:.3f}, 合格带 [{lo}, {hi}]"),
        ConstraintCheck("health", all(health_risk.get(d) != "critical" for d in on) or
                        constraints.health_mode != "derate",
                        None, "", "健康风险仅提示, 不自动停机"),
        ConstraintCheck("switching", True, float(len(starts) + len(stops)), "次",
                        "启停锁与最多次数由构造保证"),
    ]

    result.status = C.STATUS_SUCCESS
    result.reason_codes = _dedup(reasons) or [C.RC_HOLD]
    result.device_actions = actions
    result.total_flow_m3_min = round(total_flow, 2)
    result.reserve_m3_min = round(_capacity(running, eff_max) - required, 2)
    result.estimated_power_kw = round(sum(powers), 2) if powers and power_complete else None
    result.constraints = checks
    result.objective_components = {
        "energy_kwh": None,
        "switching_count": len(starts) + len(stops),
        "note": "performance model pending (B2)" if not powers else "constant-placeholder",
    }
    result.action_sequence = ([f"start:{d}" for d in starts] +
                              [f"adjust:{d}={flows.get(d, 0.0):.1f}" for d in on if d not in starts] +
                              [f"stop:{d}" for d in stops])
    result.explanation = _explain(required, reserve_extra, need, total_flow, pressure, lo, hi,
                                  starts, stops, reasons, deferred_msgs, health_risk)
    return _finalize(result, t0)


# ------------------------------------------------------------------ #
# 辅助: 守恒保持 / 文案
# ------------------------------------------------------------------ #
def _hold_conservative(result, snapshot, cfg, constraints, health_factor, health_risk, reason):
    dmap = snapshot.device_map()
    actions = []
    for d in C.DEVICE_IDS:
        ds = dmap[d]
        was = bool(ds.running)
        flow = safe_float(ds.flow_m3_min, 2) if was else 0.0
        lr = (flow / constraints.rated_capacity_m3min) if (was and flow) else 0.0
        rc = []
        if health_risk.get(d) == "critical":
            rc.append(C.RC_HEALTH_STOP_REVIEW)
        actions.append(DeviceAction(d, "hold", flow if was else 0.0,
                                    round(lr, 4), was, estimated_power_kw=None,
                                    reason_codes=rc,
                                    upper_limit_m3_min=round(
                                        constraints.validated_operating_max_m3min * health_factor[d], 2),
                                    health_factor=round(health_factor[d], 3)))
    result.status = C.STATUS_SUCCESS
    result.reason_codes = [reason]
    result.device_actions = actions
    result.total_flow_m3_min = round(sum(a.target_flow_m3_min or 0.0 for a in actions), 2)
    result.explanation = "预测缺失, 采用守恒规则: 维持当前运行状态, 不新增启停"
    result.objective_components = {"energy_kwh": None, "switching_count": 0,
                                   "note": "forecast missing hold"}
    result.action_sequence = []
    return result


def _dedup(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _explain(required, reserve_extra, need, total_flow, pressure, lo, hi,
             starts, stops, reasons, deferred_msgs, health_risk):
    parts = [f"需求(上界)={required:.1f}, 备用={reserve_extra:.1f}, 计划供气={total_flow:.1f} m3/min",
             f"母管压力={pressure:.3f} MPa, 合格带=[{lo}, {hi}]"]
    if starts:
        parts.append(f"启动: {starts}")
    if stops:
        parts.append(f"停机: {stops}")
    if not starts and not stops:
        parts.append("无启停, 维持当前运行机组")
    crit = [d for d, r in health_risk.items() if r == "critical"]
    if crit:
        parts.append(f"健康严重(仅建议人工停机, 不自动执行): {crit}")
    if deferred_msgs:
        parts.append("; ".join(deferred_msgs))
    return "; ".join(parts)
