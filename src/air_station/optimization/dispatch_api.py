"""B 模块面向 C 模块的统一调度接口 (只读封装, 不修改 C 协议)。

两种可选调度模式:
  - "b3"       : 能效优先, 复用 optimize_dispatch (B3+B4)。
  - "balanced" : 低负荷优先, 复用 UC.plan(balanced=True)。

统一输入: StationSnapshot + ForecastResult(+可选多步序列) + health + DispatchConstraints + PolicyState。
统一输出: JSON 可序列化 dict, 含 device_actions / 供气 / 功率 / 安全 / 时延 / 回退标志。
任何求解失败/超时/不可行 -> 安全回退 (B1 规则复检), 标记 is_fallback=True, 不伪装成功。
"""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd

from air_station.baseline import rules as B1_RULES
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import fallback as FB
from air_station.optimization import service as SVC
from air_station.optimization import unit_commitment as UC
from air_station.optimization.contracts import (
    DispatchConstraints,
    DispatchResult,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    StationSnapshot,
)

MODES = ("b3", "balanced", "load_balanced")
DEFAULT_MODE = "b3"

# 进程级功率估计缓存 (键: 模型路径 -> {(device, round(flow,1)): kW})。
# UC 分配枚举会大量重复估计同一 (device, flow), 无缓存会显著放大时延。
_POWER_CACHE: dict[str, dict] = {}


def _failure_status(reason_codes):
    rcs = set(reason_codes)
    if C.RC_TIMEOUT in rcs:
        return "timeout"
    if C.RC_INSUFFICIENT_DATA in rcs or C.RC_MODEL_UNAVAILABLE in rcs:
        return C.STATUS_INSUFFICIENT_DATA
    if C.RC_INFEASIBLE_CAPACITY in rcs:
        return C.STATUS_INFEASIBLE
    return "fallback"


def _fallback(snapshot, forecast, health, constraints, *, state, reason_codes,
              policy_version, power_model_path, baseline_config_path, required, t0, opt_cfg, mode):
    """安全回退: B1 规则 + 复检; 统一标记 is_fallback, 状态非 success。"""
    result = DispatchResult(status=_failure_status(reason_codes), policy_version=policy_version,
                            as_of=str(snapshot.as_of),
                            required_flow_m3_min=(required if required is not None else None))
    result.reason_codes = list(reason_codes)
    result.is_fallback = True
    result.execution_permission = "simulation_candidate_only"
    try:
        base_cfg = B1_RULES.load_config(baseline_config_path)
        fb = FB.rule_fallback(snapshot, forecast, health, constraints, baseline_config=base_cfg,
                              power_model_path=power_model_path, state=state,
                              reason_codes=reason_codes, required=required)
        SVC._attach_surge(fb["dispatch_result"], snapshot, opt_cfg.get("dispatch", {}))
        inner = fb["dispatch_result"]
        inner.is_fallback = True
        inner.execution_permission = "simulation_candidate_only"
        result.fallback_candidate = inner.to_dict()
        result.device_actions = list(inner.device_actions)
        result.transition_plan = list(inner.transition_plan)
        result.total_flow_m3_min = inner.total_flow_m3_min
        result.estimated_power_kw = inner.estimated_power_kw
        result.surge_status = inner.surge_status
        result.safety_blocked = inner.safety_blocked
    except Exception as e:  # noqa
        result.reason_codes = list(reason_codes) + [C.RC_MODEL_UNAVAILABLE]
        inner = None
        result.fallback_candidate = {"error": f"{type(e).__name__}: {e}"}
    out = result.to_dict()
    out["mode"] = mode
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return out


def _required_seq(forecast, forecast_seq, n=4):
    if forecast_seq:
        return [float(x) for x in forecast_seq]
    if forecast is None:
        return None
    base = forecast.demand_value("upper_bound")
    if base is None:
        base = forecast.predicted_flow
    if base is None:
        return None
    return [float(base)] * n


def plan_dispatch(mode: str, snapshot: StationSnapshot, forecast: Optional[ForecastResult],
                  health: list[HealthAssessment], constraints: DispatchConstraints, *,
                  state: Optional[PolicyState] = None, forecast_seq: Optional[list[float]] = None,
                  config_path: str | Path = SVC.DEFAULT_CONFIG,
                  power_model_path: Optional[str] = None,
                  baseline_config_path: Optional[str] = None,
                  timeout_ms: Optional[float] = None) -> dict:
    """统一入口。mode ∈ {"b3","balanced","load_balanced"}。返回 JSON 可序列化 dict。"""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    t0 = time.perf_counter()
    opt_cfg = SVC.load_optimization_config(config_path)
    dcfg = opt_cfg.get("dispatch", {})
    paths = opt_cfg.get("paths", {})
    pm = power_model_path or paths.get("power_model")
    bc = baseline_config_path or paths.get("baseline_config", B1_RULES.DEFAULT_CONFIG)
    to_ms = float(timeout_ms if timeout_ms is not None else dcfg.get("timeout_seconds", 30.0) * 1000.0)
    state = state or PolicyState()

    if mode == "b3":
        r = SVC.optimize_dispatch(snapshot, forecast, health, constraints, state=state,
                                  config_path=config_path, power_model_path=pm,
                                  baseline_config_path=bc)
        out = r.to_dict()
        out["mode"] = "b3"
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return out

    # balanced / load_balanced
    pv = "load-balanced-lrb-v0.1" if mode == "load_balanced" else "balanced-uc-v0.1"
    req_seq = _required_seq(forecast, forecast_seq)
    if req_seq is None:
        return _fallback(snapshot, forecast, health, constraints, state=state,
                         reason_codes=[C.RC_INSUFFICIENT_DATA], policy_version=pv,
                         power_model_path=pm, baseline_config_path=bc, required=None, t0=t0, opt_cfg=opt_cfg, mode=mode)
    req0 = req_seq[0]
    caps = CONS.build_capabilities(constraints, snapshot, health, state)
    dmap = snapshot.device_map()
    cur_on = {d for d in C.DEVICE_IDS if d in dmap and dmap[d].running}
    cur_flows = {d: (dmap[d].flow_m3_min if d in dmap else None) for d in C.DEVICE_IDS}
    try:
        ucfg = (UC.UCConfig(demand_basis="upper", spread_balance=True)
                if mode == "load_balanced" else UC.UCConfig(demand_basis="upper", balanced=True))
        bb = UC.plan(req_seq, caps, constraints, cur_on, cur_flows,
                     state.last_switch_at, snapshot.as_of,
                     model_path=pm, power_cap=0.0, cfg=ucfg,
                     cache=_POWER_CACHE.setdefault(str(pm), {}))
    except Exception as e:  # noqa
        return _fallback(snapshot, forecast, health, constraints, state=state,
                         reason_codes=[C.RC_MODEL_UNAVAILABLE, f"error:{type(e).__name__}"],
                         policy_version=pv, power_model_path=pm,
                         baseline_config_path=bc, required=req0, t0=t0, opt_cfg=opt_cfg, mode=mode)
    if (time.perf_counter() - t0) * 1000 > to_ms:
        return _fallback(snapshot, forecast, health, constraints, state=state,
                         reason_codes=[C.RC_TIMEOUT], policy_version=pv,
                         power_model_path=pm, baseline_config_path=bc, required=req0, t0=t0, opt_cfg=opt_cfg, mode=mode)
    if bb is None:
        return _fallback(snapshot, forecast, health, constraints, state=state,
                         reason_codes=[C.RC_INFEASIBLE_CAPACITY], policy_version=pv,
                         power_model_path=pm, baseline_config_path=bc, required=req0, t0=t0, opt_cfg=opt_cfg, mode=mode)

    combo = tuple(bb["seq"][0])
    alloc = bb["alloc0"]
    trans_ok, steps, trans_rc = CONS.transition_feasibility(cur_on, combo, caps, req0, constraints.reserve)
    result = DispatchResult(status=C.STATUS_SUCCESS, policy_version=pv,
                            as_of=str(snapshot.as_of), required_flow_m3_min=req0)
    result.target_combination = list(combo)
    result.device_actions = SVC._device_actions(combo, alloc, caps, cur_on,
                                                constraints.rated_capacity_m3min, dmap)
    result.transition_plan = steps
    result.total_flow_m3_min = alloc["total_flow_m3min"] if "total_flow_m3min" in alloc else sum(alloc["flows"].values())
    result.estimated_power_kw = alloc["power_kw"]
    result.estimated_energy_kwh = round(alloc["power_kw"] * constraints.demand_horizon_minutes / 60.0, 2)
    result.reserve_m3_min = round(sum(caps[d].max_flow_m3min for d in combo) - req0, 2)
    rc = list(trans_rc)
    if not trans_ok:
        rc.append(C.RC_TRANSITION_CAPACITY_GAP)
    rc += [C.RC_PRESSURE_COUNTERFACTUAL, C.RC_SURGE_NOT_ASSESSED]
    result.reason_codes = list(dict.fromkeys(rc))
    result.simulation_feasible = True
    result.requires_field_review = True
    SVC._attach_surge(result, snapshot, dcfg)
    out = result.to_dict()
    out["mode"] = mode
    out["balanced"] = (mode == "balanced")
    out["load_balanced"] = (mode == "load_balanced")
    out["sequence"] = [[d for d in c] for c in bb["seq"]]
    out["horizon_energy_kwh"] = bb.get("energy_kwh")
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    return out


def optimize_json(mode: str, *args, **kwargs) -> dict:
    return plan_dispatch(mode, *args, **kwargs)
