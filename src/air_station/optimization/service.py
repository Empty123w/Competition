"""B3 对外公共接口 (任务书 V1.1 §3.3, §7)。

唯一入口: optimize_dispatch(snapshot, forecast, health, constraints, *, policy_version, ...)
返回严格 JSON 可序列化的 DispatchResult。

约束: success 仅表示算法层仿真候选生成成功, 不等于现场安全放行。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from air_station.baseline import rules as B1_RULES
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import fallback as FB
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization import pressure as PRESS
from air_station.optimization.contracts import (
    ConstraintCheck,
    DeviceAction,
    DispatchConstraints,
    DispatchResult,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    RulAssessment,
    StationSnapshot,
)

DEFAULT_CONFIG = "configs/optimization.yaml"


def load_optimization_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[optimizer] 配置缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _forecast_status(snapshot, forecast, constraints) -> str:
    if forecast is None:
        return "missing"
    fs, ss = pd.Timestamp(forecast.as_of), pd.Timestamp(snapshot.as_of)
    if fs > ss:
        return "stale"
    if (ss - fs).total_seconds() / 60.0 > constraints.max_forecast_age_minutes:
        return "stale"
    return "ok"


def _base(snapshot, constraints, policy_version, opt_cfg, opt_cfg_path) -> DispatchResult:
    return DispatchResult(
        status=C.STATUS_SUCCESS, as_of=pd.Timestamp(snapshot.as_of).isoformat(),
        policy_version=policy_version, validation_mode="simulation",
        source_flags=dict(opt_cfg.get("dispatch", {}).get("source_flags", {})),
        pressure_setpoint_mpa=constraints.pressure_setpoint_mpa,
        model_version=opt_cfg.get("performance", {}).get("model_version"),
        surge_status=opt_cfg.get("dispatch", {}).get("surge", {}).get("status", "not_assessed_b4"),
        execution_permission=opt_cfg.get("dispatch", {}).get(
            "execution_permission", "simulation_candidate_only"),
        requires_field_review=True,
    )


def _finish(r: DispatchResult, t0: float) -> DispatchResult:
    r.elapsed_seconds = round(time.perf_counter() - t0, 4)
    return r


def _pressure_block(snapshot, total_flow, n_on, pressure_model_path):
    if snapshot.header_pressure_mpa is None:
        return {"status": C.RC_INSUFFICIENT_DATA, "message": "母管压力缺失, 压力影响待复核"}
    return PRESS.predict_pressure(snapshot.header_pressure_mpa, total_flow_m3_min=total_flow,
                                  running_count=n_on, model_path=pressure_model_path)


def _baseline_comparison(snapshot, forecast, health, constraints, baseline_cfg,
                         power_model_path, state, best_power):
    try:
        bc = FB.baseline_candidate_power(snapshot, forecast, health, constraints,
                                         baseline_config=baseline_cfg,
                                         power_model_path=power_model_path, state=state)
    except Exception as e:  # noqa
        return {"status": C.RC_MODEL_UNAVAILABLE, "message": str(e)}
    bp = bc.get("estimated_power_kw")
    out = {"baseline": bc, "optimized_power_kw": best_power, "baseline_power_kw": bp}
    if bp is not None and best_power is not None and bp > 0:
        out["power_delta_kw"] = round(best_power - bp, 2)
        out["power_reduction_pct"] = round((bp - best_power) / bp * 100.0, 3)
        out["basis"] = "model_estimate (simulation), same demand/reserve/pressure/scenario"
    return out


def _device_actions(combo, alloc, caps, current_on, rated, dmap):
    actions = []
    for d in C.DEVICE_IDS:
        on = d in combo
        was = d in current_on
        if on:
            flow = float(alloc["flows"].get(d, caps[d].min_flow_m3min))
            lr = flow / rated if rated else None
            power = alloc["device_power"].get(d)
            if was:
                prev = float(dmap[d].flow_m3_min) if dmap[d].flow_m3_min is not None else flow
                action = "adjust" if abs(flow - prev) > 1.0 else "hold"
            else:
                action = "start"
            actions.append(DeviceAction(d, action, round(flow, 2),
                                        round(lr, 4) if lr is not None else None, True,
                                        estimated_power_kw=power,
                                        upper_limit_m3_min=round(caps[d].max_flow_m3min, 2),
                                        health_factor=round(caps[d].health_factor, 3)))
        else:
            actions.append(DeviceAction(d, "stop" if was else "hold", 0.0, 0.0, False,
                                        estimated_power_kw=0.0,
                                        upper_limit_m3_min=round(caps[d].max_flow_m3min, 2),
                                        health_factor=round(caps[d].health_factor, 3)))
    return actions


def optimize_dispatch(
    snapshot: StationSnapshot,
    forecast: Optional[ForecastResult],
    health: list[HealthAssessment],
    constraints: DispatchConstraints,
    *,
    policy_version: str = "b3-optimizer-v0.1",
    rul: Optional[list[RulAssessment]] = None,
    state: Optional[PolicyState] = None,
    config_path: str | Path = DEFAULT_CONFIG,
    power_model_path: Optional[str] = None,
    pressure_model_path: Optional[str] = None,
    baseline_config_path: Optional[str] = None,
) -> DispatchResult:
    t0 = time.perf_counter()
    opt_cfg = load_optimization_config(config_path)
    dcfg = opt_cfg.get("dispatch", {})
    paths = opt_cfg.get("paths", {})
    power_model_path = power_model_path or paths.get("power_model", PERF.DEFAULT_POWER_MODEL)
    pressure_model_path = pressure_model_path or paths.get("pressure_model", PRESS.DEFAULT_PRESSURE_MODEL)
    baseline_config_path = baseline_config_path or paths.get("baseline_config", B1_RULES.DEFAULT_CONFIG)
    timeout = float(dcfg.get("timeout_seconds", 30.0))
    result = _base(snapshot, constraints, policy_version, opt_cfg, config_path)
    result.health_ids = [h.device_id for h in (health or [])]

    def _with_fallback(status, reason_codes, required=None):
        result.status = status
        result.reason_codes = list(reason_codes)
        if dcfg.get("fallback", {}).get("enabled", True):
            try:
                base_cfg = B1_RULES.load_config(baseline_config_path)
                fb = FB.rule_fallback(snapshot, forecast, health, constraints,
                                      baseline_config=base_cfg, power_model_path=power_model_path,
                                      state=state, reason_codes=reason_codes, required=required)
                result.fallback_candidate = fb["summary"]
            except Exception as e:  # noqa
                result.fallback_candidate = {"status": C.RC_MODEL_UNAVAILABLE, "message": str(e)}
        return _finish(result, t0)

    # 1) 输入合法性
    dmap = snapshot.device_map()
    if set(dmap.keys()) - set(C.DEVICE_IDS) or any(d not in dmap for d in C.DEVICE_IDS):
        return _with_fallback(C.STATUS_INVALID_INPUT, [C.RC_INVALID_INPUT])

    # 2) 模型可用性
    if not Path(power_model_path).exists():
        return _with_fallback(C.STATUS_INSUFFICIENT_DATA, [C.RC_MODEL_UNAVAILABLE])

    # 3) 预测状态
    fc_status = _forecast_status(snapshot, forecast, constraints)
    if fc_status == "stale":
        return _with_fallback(C.STATUS_INSUFFICIENT_DATA, [C.RC_STALE_FORECAST])
    if fc_status == "missing":
        return _with_fallback(C.STATUS_INSUFFICIENT_DATA, [C.RC_FORECAST_MISSING_HOLD])

    required = forecast.demand_value(constraints.demand_basis)
    if required is None:
        return _with_fallback(C.STATUS_INSUFFICIENT_DATA, [C.RC_INSUFFICIENT_DATA])
    result.required_flow_m3_min = required
    result.forecast_id = forecast.forecast_id

    # 4) 枚举 + 分配 + 选优
    search = OPT.search_combinations(snapshot, required, constraints, health, state, dcfg,
                                     power_model_path, rul=rul)
    result.target_combination = list(search["best"]["combo"]) if search["best"] else []
    if search["best"] is None:
        model_err = bool(search["results"]) and all(r.get("model_error") for r in search["results"])
        status = C.STATUS_INSUFFICIENT_DATA if model_err else C.STATUS_INFEASIBLE
        if model_err:
            rc = [C.RC_MODEL_UNAVAILABLE]
        else:
            rc = list(dict.fromkeys(rc for r in search["results"] for rc in r.get("reason_codes", [])))
            rc = rc or [C.RC_INFEASIBLE_CAPACITY]
        return _with_fallback(status, rc, required=required)

    # 超时保护: 求解核心完成后立即判定 (含分配与选型)
    if timeout <= 0.0 or (time.perf_counter() - t0) > timeout:
        return _with_fallback(C.STATUS_TIMEOUT, [C.RC_TIMEOUT], required=required)

    best = search["best"]
    caps = search["capabilities"]
    current_on = search["current_on"]
    combo = best["combo"]
    alloc = best["allocation"]

    # 5) 切换过渡检查
    trans_ok, steps, trans_rc = CONS.transition_feasibility(current_on, combo, caps,
                                                            required, constraints.reserve)
    result.transition_plan = steps

    # 6) 压力 (反事实能力不足, 待复核)
    pp = _pressure_block(snapshot, alloc["total_flow_m3min"], len(combo), pressure_model_path)
    result.pressure_prediction = pp

    # 7) 结果组装
    result.status = C.STATUS_SUCCESS
    reason_codes = list(best["reason_codes"]) + trans_rc
    if not trans_ok:
        reason_codes.append(C.RC_TRANSITION_CAPACITY_GAP)
    if pp.get("status") != "success":
        reason_codes.append(C.RC_INSUFFICIENT_DATA)
    reason_codes += [C.RC_PRESSURE_COUNTERFACTUAL, C.RC_SURGE_NOT_ASSESSED]
    hmap = {h.device_id: h.internal_risk for h in (health or [])}
    if constraints.health_mode == "observe" and any(hmap.get(d) == "critical" for d in combo):
        reason_codes.append(C.RC_HEALTH_STOP_REVIEW)
    if dcfg.get("rul", {}).get("enabled") and rul:
        reason_codes.append(C.RC_RUL_SIMULATION)
    reason_codes += list(best.get("warnings", []))
    result.reason_codes = list(dict.fromkeys(reason_codes))
    result.device_actions = _device_actions(combo, alloc, caps, current_on,
                                            constraints.rated_capacity_m3min, dmap)
    result.total_flow_m3_min = alloc["total_flow_m3min"]
    result.estimated_power_kw = alloc["power_kw"]
    result.estimated_energy_kwh = round(alloc["power_kw"] * constraints.demand_horizon_minutes / 60.0, 2)
    result.reserve_m3_min = round(best["assessment"].capacity_m3min - required, 2)
    result.simulation_feasible = True
    result.requires_field_review = True

    checks = [
        ConstraintCheck("demand", alloc.get("demand_met", False),
                        alloc["total_flow_m3min"] - required, "m3/min",
                        f"供气 {alloc['total_flow_m3min']:.1f} vs 需求 {required:.1f}"),
        ConstraintCheck("capacity", best["assessment"].capacity_m3min + 1e-6 >= best["assessment"].need_m3min,
                        best["assessment"].margin_m3min, "m3/min", "容量 vs 需求+备用"),
        ConstraintCheck("reserve", True if constraints.reserve.mode == "none"
                        else best["assessment"].capacity_m3min - required >= -1e-6,
                        best["assessment"].capacity_m3min - required, "m3/min",
                        f"备用模式={constraints.reserve.mode}"),
        ConstraintCheck("transition", bool(trans_ok), None, "", "切换过程供气"),
        ConstraintCheck("health", True, None, "",
                        "健康为无监督软惩罚, 不自动停机"),
        ConstraintCheck("switching", True, float(best["n_switches"]), "次", "启停锁由构造保证"),
    ]
    result.constraints = checks
    result.objective_components = {
        "candidate_type": "optimized",
        "terms": best["terms"],
        "weights": dcfg.get("weights", {}),
        "total_objective": best["total_objective"],
        "power_kw_estimate": alloc["power_kw"],
        "energy_kwh_estimate": result.estimated_energy_kwh,
        "note": "分项已归一化后加权; 功率来自 B2 工程近似模型",
    }
    result.action_sequence = ([f"start:{d}" for d in best["starts"]]
                              + [f"adjust:{d}={alloc['flows'][d]:.1f}" for d in combo
                                 if d not in best["starts"]]
                              + [f"stop:{d}" for d in best["stops"]])
    result.baseline_comparison = _baseline_comparison(snapshot, forecast, health, constraints,
                                                      B1_RULES.load_config(baseline_config_path),
                                                      power_model_path, state, alloc["power_kw"])
    result.explanation = (
        f"最优组合={list(combo)}, 供气={alloc['total_flow_m3min']:.1f} m3/min, "
        f"预计功率={alloc['power_kw']:.1f} kW, 切换={best['n_switches']} 次; "
        f"压力待复核(反事实不支持), 喘振待 B4 评估; 仅仿真候选, 未经现场放行")

    return _finish(result, t0)


def optimize_json(*args, **kwargs) -> dict:
    """便捷包装: 返回可 JSON 序列化的 dict。"""
    return optimize_dispatch(*args, **kwargs).to_dict()


if __name__ == "__main__":
    print(json.dumps({"module": "optimization.service",
                      "entry": "optimize_dispatch(snapshot, forecast, health, constraints, *, policy_version)"},
                     ensure_ascii=False, indent=2))
