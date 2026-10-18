"""B3 多机调度优化核心 (任务书 V1.1 §7.1/§7.2)。

方法: 显式枚举 4 种开机组合 -> 硬约束可行性筛选 -> 连续负荷分配
      -> 归一化目标比较 -> 选择最优可行组合。

目标 (分项归一化后加权, 不同量纲不相加):
  total = w_power * (P/P_ref) + w_switch * (n_sw/N_ref) + w_health * H + w_rul * R

不实现强化学习; 求解规模仅 2 台机组, 枚举 + 解析分配即可。
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.contracts import (
    DispatchConstraints,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    RulAssessment,
    StationSnapshot,
)


def sum_energy_kwh(powers_kw, step_minutes: float) -> float:
    """把逐点功率(kW)按步长换算为电量(kWh)。

    energy_kwh = Σ power_kw * (step_minutes / 60)
    注意: 直接把 kW 相加**不是** kWh; 1 分钟步长需乘 1/60。
    """
    dt_h = float(step_minutes) / 60.0
    return float(sum(float(p) for p in powers_kw) * dt_h)


def _est(device_id: str, flow: float, power_model_path: str) -> dict:
    return PERF.estimate_power(device_id, flow, model_path=power_model_path)


def allocate_flow(combo, required: float, caps: dict, power_model_path: str,
                  estimate=None) -> dict:
    """在给定组合内按边际功率贪心分配需求流量, 返回 flows/power/warnings。"""
    est = estimate or (lambda d, q: _est(d, q, power_model_path))
    if not combo:
        return {"flows": {}, "total_flow_m3min": 0.0, "power_kw": 0.0,
                "ok": True, "warnings": [], "device_power": {}}
    flows = {d: caps[d].min_flow_m3min for d in combo}
    slopes, warns = {}, []
    for d in combo:
        lo, hi = caps[d].min_flow_m3min, caps[d].max_flow_m3min
        r_lo, r_hi = est(d, lo), est(d, hi)
        if r_lo["status"] != "success" or r_hi["status"] != "success":
            return {"flows": {}, "ok": False, "reason": r_lo.get("status") or r_hi.get("status"),
                    "warnings": [C.RC_MODEL_UNAVAILABLE]}
        slopes[d] = (r_hi["estimated_power_kw"] - r_lo["estimated_power_kw"]) / max(hi - lo, 1e-6)
    remaining = required - sum(flows.values())
    for d in sorted(combo, key=lambda x: (slopes[x], x)):   # 先填边际成本低者
        if remaining <= 1e-9:
            break
        room = caps[d].max_flow_m3min - flows[d]
        add = min(max(room, 0.0), remaining)
        flows[d] += add
        remaining -= add
    power, dpower = 0.0, {}
    for d in combo:
        r = est(d, flows[d])
        if r["status"] != "success":
            return {"flows": {}, "ok": False, "reason": r.get("status"),
                    "warnings": [C.RC_MODEL_UNAVAILABLE]}
        dpower[d] = r["estimated_power_kw"]
        power += r["estimated_power_kw"]
        for w in r.get("warnings", []):
            warns.append(w)
    total_flow = float(sum(flows.values()))
    return {"flows": {d: round(flows[d], 3) for d in combo}, "total_flow_m3min": round(total_flow, 2),
            "power_kw": round(power, 2), "device_power": dpower, "ok": True,
            "warnings": list(dict.fromkeys(warns)),
            "demand_met": bool(total_flow + 1e-6 >= required)}


def _health_penalty(risk: str, penalty_map: dict) -> float:
    return float(penalty_map.get(risk, 0.0))


def _rul_penalty(combo, rul_map: dict, threshold: float) -> tuple[float, int]:
    if not combo:
        return 0.0, 0
    vals, n = [], 0
    for d in combo:
        r = rul_map.get(d)
        if r is None or r.estimated_rul_hours is None:
            continue
        vals.append(max(0.0, (threshold - float(r.estimated_rul_hours)) / threshold))
        n += 1
    return (sum(vals) / len(vals) if vals else 0.0), n


def evaluate_combo(combo, caps, required, reserve, current_on, snapshot, health,
                   opt_cfg, power_model_path, rul_map) -> dict:
    """评估一个组合: 可行性 + 分配 + 归一化目标分项。"""
    assess = CONS.assess_combination(combo, caps, required, reserve)
    if not assess.feasible:
        return {"combo": tuple(combo), "feasible": False, "reason_codes": assess.reason_codes,
                "assessment": assess}
    alloc = allocate_flow(combo, required, caps, power_model_path)
    if not alloc["ok"]:
        return {"combo": tuple(combo), "feasible": False,
                "reason_codes": [alloc.get("reason", C.RC_MODEL_UNAVAILABLE)],
                "assessment": assess, "model_error": True}
    # 单机功率硬上限 (V1.1 §7.1 "最大功率")
    cap_kw = float(opt_cfg.get("power_cap_kw_per_device", 0.0) or 0.0)
    if cap_kw > 0.0:
        over = [d for d in combo if alloc["device_power"].get(d, 0.0) > cap_kw + 1e-6]
        if over:
            return {"combo": tuple(combo), "feasible": False,
                    "reason_codes": [C.RC_POWER_OVER_CAP], "assessment": assess,
                    "over_power_devices": over}
    # 切换次数
    starts = [d for d in combo if d not in current_on]
    stops = [d for d in current_on if d not in combo]
    n_switches = len(starts) + len(stops)
    # 分项
    w = opt_cfg["weights"]
    nz = opt_cfg["normalization"]
    power_term = alloc["power_kw"] / float(nz["power_ref_kw"])
    switch_term = n_switches / float(nz["switch_ref"])
    hmap = {h.device_id: h.internal_risk for h in (health or [])}
    health_term = (sum(_health_penalty(hmap.get(d, "normal"), opt_cfg["health_penalty_map"])
                       for d in combo) / len(combo)) if combo else 0.0
    rul_term, rul_n = (0.0, 0)
    if opt_cfg.get("rul", {}).get("enabled"):
        rul_term, rul_n = _rul_penalty(combo, rul_map, float(opt_cfg["rul"]["threshold_hours"]))
    total = (float(w["power"]) * power_term + float(w["switch"]) * switch_term
             + float(w["health"]) * health_term + float(w["rul"]) * rul_term)
    return {
        "combo": tuple(combo), "feasible": True, "reason_codes": [],
        "assessment": assess, "allocation": alloc,
        "starts": starts, "stops": stops, "n_switches": n_switches,
        "terms": {"power": round(power_term, 5), "switch": round(switch_term, 5),
                  "health": round(health_term, 5), "rul": round(rul_term, 5)},
        "power_kw_term": alloc["power_kw"],
        "total_objective": round(total, 6),
        "rul_devices_used": rul_n,
        "warnings": alloc.get("warnings", []),
    }


def search_combinations(snapshot: StationSnapshot, required: float,
                        constraints: DispatchConstraints, health, state,
                        opt_cfg: dict, power_model_path: str, rul=None) -> dict:
    """枚举四种组合并返回全部评估结果与最优项。"""
    caps = CONS.build_capabilities(constraints, snapshot, health, state)
    current_on = tuple(d for d in C.DEVICE_IDS if caps[d].currently_running)
    rul_map = {r.device_id: r for r in (rul or [])}
    results = [evaluate_combo(combo, caps, required, constraints.reserve, current_on,
                              snapshot, health, opt_cfg, power_model_path, rul_map)
               for combo in CONS.COMBINATIONS]
    feasible = [r for r in results if r.get("feasible")]
    best = min(feasible, key=lambda r: (r["total_objective"], r["combo"])) if feasible else None
    return {"capabilities": caps, "current_on": current_on, "results": results,
            "feasible": feasible, "best": best, "rul_map": rul_map}
