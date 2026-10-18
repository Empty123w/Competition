"""B4 喘振风险代理评估 (任务书 V1.1 §8)。

对外入口: assess_surge_risk(snapshot, candidate, *, boundary_version) -> SurgeAssessment

关键约定:
  - 代理评分/统计边界, **不是**厂家真实喘振线;
  - 关键输入缺失、超出边界有效域或证据不足 -> risk_level="unknown", 不得默认 low;
  - high 与 unknown 均触发 safety_blocked (需人工复核), 不自动执行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from air_station.optimization import contracts as C
from air_station.optimization.contracts import (
    DispatchResult,
    StationSnapshot,
    SurgeAssessment,
)
from air_station.safety import boundaries as BD

DEFAULT_CONFIG = "configs/safety.yaml"
DEFAULT_BOUNDARY = "artifacts/models/surge_boundary.json"

_RANK = {"low": 0, "medium": 1, "high": 2}
_LEVEL = {0: "low", 1: "medium", 2: "high"}
_WORST = {"unknown": 3, "high": 2, "medium": 1, "low": 0}

_LIMIT_SUGGESTION = {
    "low": "代理裕度充足; 低风险仅为模型判断, 非现场安全认证",
    "medium": "接近低负荷代理边界或存在风险因子, 建议提高流量/人工复核",
    "high": "低于代理边界或强风险因子, 建议提升流量或停机, 禁止自动执行",
    "unknown": "边界或关键测量缺失, 无法评估, 需人工复核补充数据",
}


def _base_level(margin: float, warn: float) -> str:
    if margin < 0:
        return "high"
    if margin < warn:
        return "medium"
    return "low"


def _severity_rank(cfg: dict, factor: str) -> int:
    lvl = cfg.get("triggers", {}).get("severity", {}).get(factor, "low")
    return _RANK.get(lvl, 0)


def assess_surge_risk(
    snapshot: StationSnapshot,
    candidate: DispatchResult,
    *,
    boundary_version: Optional[str] = None,
    config_path: str | Path = DEFAULT_CONFIG,
    boundary_path: str | Path = DEFAULT_BOUNDARY,
) -> SurgeAssessment:
    cfg = BD.load_config(config_path)
    warn = float(cfg["boundary"].get("warn_margin", 0.10))
    trig = cfg.get("triggers", {})
    block_high = bool(cfg.get("evaluation", {}).get("block_on_high", True))
    block_unknown = bool(cfg.get("evaluation", {}).get("block_on_unknown", True))
    requested_version = boundary_version or cfg["meta"]["boundary_version"]

    capability = {
        "boundary_type": "proxy",
        "is_real_surge_line": False,
        "counterfactual_pressure": "unsupported",
        "statement": "代理评分与统计边界, 非真实喘振线; 低风险不等于现场安全认证",
    }
    try:
        artifact = BD.load_boundary_artifact(boundary_path)
    except FileNotFoundError as e:
        return SurgeAssessment(
            status="unknown", boundary_source="missing", validation_mode="simulation",
            safety_blocked=True, counts={"low": 0, "medium": 0, "high": 0, "unknown": 1},
            reason_codes=[C.RC_SURGE_BOUNDARY_UNKNOWN], capability={**capability, "message": str(e)})

    effective_version = artifact.get("boundary_version", "")
    version_ok = (requested_version == effective_version)
    dmap = snapshot.device_map() if snapshot else {}
    current = dmap
    start_devices = {s.get("device_id") for s in (candidate.transition_plan or [])
                     if s.get("step") == "start"}

    devices: list[dict] = []
    for a in candidate.device_actions:
        if not a.running_after:
            continue
        did = a.device_id
        ds = current.get(did)
        planned = a.target_flow_m3_min
        if ds is None:
            # 待启动机组无当前排气压力, 用母管压力作代理
            pressure = snapshot.header_pressure_mpa
        else:
            pressure = ds.discharge_pressure_mpa
        factors: list[str] = []
        rc: list[str] = []
        margin = None
        boundary_flow = None
        bsrc = cfg["boundary"].get("source", "engineering_assumed")

        unknown_reason = None
        if not version_ok:
            unknown_reason = C.RC_SURGE_BOUNDARY_UNKNOWN
        elif planned is None or pressure is None:
            unknown_reason = C.RC_SURGE_DATA_MISSING
        elif not BD._pressure_in_domain(float(pressure), cfg):
            unknown_reason = C.RC_SURGE_BOUNDARY_UNKNOWN

        if unknown_reason is None:
            q_b, bsrc = BD.boundary_flow(did, float(pressure), cfg, artifact)
            if q_b is None or q_b <= 0:
                unknown_reason = C.RC_SURGE_BOUNDARY_UNKNOWN
            else:
                boundary_flow = q_b
                margin = (float(planned) - q_b) / q_b

        if unknown_reason is not None:
            devices.append({
                "device_id": did, "risk_level": "unknown", "risk_margin": None,
                "margin_definition": "(planned_flow - boundary_flow)/boundary_flow",
                "planned_flow_m3min": planned, "boundary_flow_m3min": boundary_flow,
                "pressure_mpa": pressure, "trigger_factors": [],
                "boundary_source": bsrc, "boundary_version": effective_version,
                "limit_suggestion": _LIMIT_SUGGESTION["unknown"],
                "validation_mode": "simulation", "reason_codes": [unknown_reason],
            })
            continue

        rank = _RANK[_base_level(margin, warn)]
        # 触发因素 (证据, 工程阈值)
        q_env = BD.historical_envelope_flow(did, float(pressure), artifact)
        if trig.get("below_historical_envelope", True) and q_env is not None and float(planned) < q_env:
            factors.append("below_historical_envelope")
        if ds and ds.igv_percent is not None and ds.igv_percent < float(trig.get("igv_low_percent", 35.0)):
            factors.append("igv_low")
        if ds and ds.bov_closed_percent is not None and ds.bov_closed_percent < float(
                trig.get("bov_open_closed_percent", 95.0)):
            factors.append("bov_open")
        if ds and ds.flow_m3_min is not None and abs(float(planned) - float(ds.flow_m3_min)) > float(
                trig.get("rapid_load_change_m3min", 40.0)):
            factors.append("rapid_change")
        if trig.get("start_transition", True) and (a.action == "start" or did in start_devices):
            factors.append("start_transition")
        for f in factors:
            rank = max(rank, _severity_rank(cfg, f))
        level = _LEVEL[rank]
        if level == "high":
            rc.append(C.RC_SURGE_HIGH)
        elif level == "medium":
            rc.append(C.RC_SURGE_MEDIUM)

        devices.append({
            "device_id": did, "risk_level": level,
            "risk_margin": round(margin, 4),
            "margin_definition": "(planned_flow - boundary_flow)/boundary_flow",
            "planned_flow_m3min": round(float(planned), 2),
            "boundary_flow_m3min": round(boundary_flow, 2),
            "pressure_mpa": round(float(pressure), 4),
            "historical_envelope_m3min": round(q_env, 2) if q_env is not None else None,
            "trigger_factors": factors,
            "boundary_source": bsrc, "boundary_version": effective_version,
            "limit_suggestion": _LIMIT_SUGGESTION[level],
            "validation_mode": "simulation", "reason_codes": rc,
        })

    counts = {"low": 0, "medium": 0, "high": 0, "unknown": 0}
    for d in devices:
        counts[d["risk_level"]] = counts.get(d["risk_level"], 0) + 1
    safety_blocked = (block_high and counts["high"] > 0) or (block_unknown and counts["unknown"] > 0)
    reason_codes = []
    if counts["high"]:
        reason_codes.append(C.RC_SURGE_HIGH)
    if counts["unknown"]:
        reason_codes.append(C.RC_SURGE_UNKNOWN)
    if counts["medium"]:
        reason_codes.append(C.RC_SURGE_MEDIUM)
    if not version_ok:
        reason_codes.append(C.RC_SURGE_BOUNDARY_UNKNOWN)

    return SurgeAssessment(
        status="success" if devices else "no_active_devices",
        boundary_version=effective_version,
        boundary_source=cfg["boundary"].get("source", "engineering_assumed"),
        validation_mode="simulation", devices=devices, safety_blocked=safety_blocked,
        counts=counts, reason_codes=list(dict.fromkeys(reason_codes)), capability=capability,
    )


def worst_level(assessment: SurgeAssessment) -> str:
    levels = [d.get("risk_level", "unknown") for d in assessment.devices] or ["low"]
    return max(levels, key=lambda x: _WORST.get(x, 3))
