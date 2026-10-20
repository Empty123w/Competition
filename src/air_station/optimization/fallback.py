"""B3 回退候选 (任务书 V1.1 §7.2)。

求解失败/模型不可用时, 用 B1 规则生成**经过重新检查**的回退候选。
回退候选统一标记 is_fallback=True 与 execution_permission=simulation_candidate_only,
不得伪装成优化成功, 也不默认允许现场执行。
"""
from __future__ import annotations

from typing import Optional

from air_station.baseline import rules as B1_RULES
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.contracts import (
    DispatchConstraints,
    DispatchResult,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    StationSnapshot,
)


def _estimate_flow_power(device_actions, constraints: DispatchConstraints,
                         power_model_path: str) -> tuple[Optional[float], list[str]]:
    total, warns, complete = 0.0, [], True
    for a in device_actions:
        if not a.running_after:
            continue
        r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0,
                                model_path=power_model_path)
        if r["status"] == "success":
            total += float(r["estimated_power_kw"])
        else:
            complete = False
        warns.extend(r.get("warnings", []))
    return (round(total, 2) if complete else None), list(dict.fromkeys(warns))


def recheck(candidate: DispatchResult, constraints: DispatchConstraints,
            snapshot: StationSnapshot, required: Optional[float]) -> dict:
    """对回退候选做供气/容量复检。"""
    flow = candidate.total_flow_m3_min or 0.0
    checks = {
        "demand_ok": (required is None) or (flow + 1e-6 >= required),
        "has_actions": bool(candidate.device_actions),
        "flow_nonneg": all((a.target_flow_m3_min or 0.0) >= 0 for a in candidate.device_actions),
    }
    return {"checks": checks, "passed": all(checks.values())}


def rule_fallback(snapshot: StationSnapshot, forecast: Optional[ForecastResult],
                  health: list[HealthAssessment], constraints: DispatchConstraints, *,
                  baseline_config: dict, power_model_path: str,
                  state: Optional[PolicyState] = None,
                  reason_codes: Optional[list[str]] = None,
                  required: Optional[float] = None) -> dict:
    """生成 B1 规则回退候选并复检。"""
    base = B1_RULES.decide(snapshot, forecast, health, constraints, baseline_config,
                           state or PolicyState())
    power, warns = _estimate_flow_power(base.device_actions, constraints, power_model_path)
    base.is_fallback = True
    base.execution_permission = "simulation_candidate_only"
    base.simulation_feasible = (base.status == C.STATUS_SUCCESS)
    base.requires_field_review = True
    base.estimated_power_kw = power
    if base.status == C.STATUS_SUCCESS:
        base.reason_codes = list(dict.fromkeys(list(base.reason_codes) + (reason_codes or [])
                                               + [C.RC_FALLBACK_RULE]))
    rc = recheck(base, constraints, snapshot, required)
    base.objective_components = dict(base.objective_components or {})
    base.objective_components.update({"candidate_type": "rule_fallback",
                                      "fallback_recheck_passed": rc["passed"]})
    if warns:
        base.source_flags = dict(base.source_flags or {})
        base.source_flags["power_estimate_warnings"] = warns
    return {"dispatch_result": base, "recheck": rc,
            "summary": {"status": base.status, "total_flow_m3_min": base.total_flow_m3_min,
                        "estimated_power_kw": power, "recheck_passed": rc["passed"],
                        "candidate_type": "rule_fallback"}}


def baseline_candidate_power(snapshot, forecast, health, constraints, *,
                             baseline_config: dict, power_model_path: str,
                             state: Optional[PolicyState] = None) -> dict:
    """计算 B1 规则基线的预计功率, 供 B3 同工况对比。"""
    fb = rule_fallback(snapshot, forecast, health, constraints,
                       baseline_config=baseline_config, power_model_path=power_model_path,
                       state=state)
    r = fb["dispatch_result"]
    return {"status": r.status, "estimated_power_kw": r.estimated_power_kw,
            "total_flow_m3_min": r.total_flow_m3_min,
            "combination": [a.device_id for a in r.device_actions if a.running_after],
            "reason_codes": list(r.reason_codes)}
