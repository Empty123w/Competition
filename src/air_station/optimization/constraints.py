"""B3 硬约束与机组组合可行性 (任务书 V1.1 §7.1/§7.3)。

复用 B1 的 DispatchConstraints (同一套需求/备用/压力/容量/启停/健康口径),
保证 B1 规则基线与 B3 优化器在**相同约束**下比较。

真实设备仅 4#、5#; 枚举四种开机组合。区分:
  - extra 备用: 额外可用流量 (需求 + 备用 <= 容量)
  - N-1 备用: 单机失效后仍能满足需求
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from air_station.optimization import contracts as C
from air_station.optimization.contracts import (
    DispatchConstraints,
    HealthAssessment,
    PolicyState,
    ReservePolicy,
    RulAssessment,
    StationSnapshot,
)

# 确定性的组合顺序: 停机 / 单机 / 双机
COMBINATIONS: tuple[tuple[str, ...], ...] = ((), ("4#",), ("5#",), ("4#", "5#"))


@dataclass
class DeviceCapability:
    device_id: str
    available: bool
    currently_running: bool
    min_flow_m3min: float
    max_flow_m3min: float
    health_factor: float
    internal_risk: str
    can_start: bool
    can_stop: bool

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id, "available": self.available,
            "currently_running": self.currently_running,
            "min_flow_m3min": round(self.min_flow_m3min, 2),
            "max_flow_m3min": round(self.max_flow_m3min, 2),
            "health_factor": round(self.health_factor, 3),
            "internal_risk": self.internal_risk,
            "can_start": self.can_start, "can_stop": self.can_stop,
        }


@dataclass
class CombinationAssessment:
    combo: tuple[str, ...]
    feasible: bool
    reason_codes: list[str] = field(default_factory=list)
    capacity_m3min: float = 0.0
    need_m3min: float = 0.0
    reserve_m3min: float = 0.0
    n1_capacity_m3min: Optional[float] = None
    margin_m3min: float = 0.0

    def to_dict(self) -> dict:
        return {
            "combo": list(self.combo), "feasible": self.feasible,
            "reason_codes": list(self.reason_codes),
            "capacity_m3min": round(self.capacity_m3min, 2),
            "need_m3min": round(self.need_m3min, 2),
            "reserve_m3min": round(self.reserve_m3min, 2),
            "n1_capacity_m3min": (round(self.n1_capacity_m3min, 2)
                                  if self.n1_capacity_m3min is not None else None),
            "margin_m3min": round(self.margin_m3min, 2),
        }


def _health_factor(device_id: str, health: dict, constraints: DispatchConstraints):
    h = health.get(device_id)
    if h is None:
        return 1.0, "normal"
    risk = h.internal_risk
    if constraints.health_mode != "derate":
        return 1.0, risk
    return float(constraints.health_derate_map.get(risk, 1.0)), risk


def _lock_capability(device_id: str, running: bool, as_of: pd.Timestamp,
                     state: PolicyState, constraints: DispatchConstraints):
    last = state.last_switch_at.get(device_id) if state else None
    if last is None:
        return (not running), running
    delta = (as_of - pd.Timestamp(last)).total_seconds() / 60.0
    if running:
        return False, delta >= constraints.min_run_minutes
    return delta >= constraints.min_stop_minutes, False


def _switch_budget_ok(state: PolicyState, as_of: pd.Timestamp,
                      constraints: DispatchConstraints) -> bool:
    if state is None:
        return True
    recent = [t for t in state.recent_switch_times
              if (as_of - pd.Timestamp(t)).total_seconds() / 60.0 <= 60.0]
    return len(recent) < constraints.max_switches_per_hour


def build_capabilities(constraints: DispatchConstraints, snapshot: StationSnapshot,
                       health: list[HealthAssessment], state: Optional[PolicyState] = None
                       ) -> dict[str, DeviceCapability]:
    health_map = {h.device_id: h for h in (health or [])}
    as_of = pd.Timestamp(snapshot.as_of) if snapshot else pd.Timestamp.utcnow()
    dmap = snapshot.device_map() if snapshot else {}
    caps: dict[str, DeviceCapability] = {}
    for did in C.DEVICE_IDS:
        ds = dmap.get(did)
        running = bool(ds.running) if ds else False
        quality_ok = bool(ds.quality_valid) if ds else False
        factor, risk = _health_factor(did, health_map, constraints)
        # 质量无效或 derate 后上限为 0 -> 不可用
        eff_max = float(constraints.validated_operating_max_m3min) * factor
        available = quality_ok and eff_max > 0
        if constraints.health_mode == "derate" and risk == "critical":
            available = False          # derate 模式下严重风险不参与优化(仅提示)
        can_start, can_stop = _lock_capability(did, running, as_of, state, constraints)
        if not available:
            can_start = False
        caps[did] = DeviceCapability(
            device_id=did, available=available, currently_running=running,
            min_flow_m3min=float(constraints.validated_min_m3min),
            max_flow_m3min=eff_max, health_factor=factor, internal_risk=risk,
            can_start=bool(can_start), can_stop=bool(can_stop),
        )
    return caps


def _capacity(caps: dict, combo) -> float:
    return float(sum(caps[d].max_flow_m3min for d in combo))


def assess_combination(combo, caps: dict, required: float,
                       reserve: ReservePolicy) -> CombinationAssessment:
    reasons: list[str] = []
    for d in combo:
        if not caps[d].available:
            reasons.append(C.RC_DEVICE_UNAVAILABLE)
    need = required + reserve.extra_m3_min(required)
    capacity = _capacity(caps, combo)
    n1 = None
    if reserve.mode == "n_minus_one" and combo:
        n1 = capacity - max(caps[d].max_flow_m3min for d in combo)
    feasible = True
    if C.RC_DEVICE_UNAVAILABLE in reasons:
        feasible = False
    if not combo and required > 1e-6:
        feasible = False
        reasons.append(C.RC_INFEASIBLE_CAPACITY)
    if capacity + 1e-6 < need:
        feasible = False
        reasons.append(C.RC_INFEASIBLE_CAPACITY)
    if reserve.mode == "n_minus_one":
        if not combo or n1 is None or n1 + 1e-6 < required:
            feasible = False
            reasons.append(C.RC_RESERVE_N1)
    return CombinationAssessment(
        combo=tuple(combo), feasible=feasible, reason_codes=_dedup(reasons),
        capacity_m3min=capacity, need_m3min=need,
        reserve_m3min=capacity - required, n1_capacity_m3min=n1,
        margin_m3min=capacity - need,
    )


def enumerate_combinations(required: float, caps: dict,
                           reserve: ReservePolicy) -> list[CombinationAssessment]:
    return [assess_combination(combo, caps, required, reserve) for combo in COMBINATIONS]


def transition_feasibility(current_on, target_on, caps: dict, required: float,
                           reserve: ReservePolicy) -> tuple[bool, list[dict], list[str]]:
    """构造分步切换计划并检查每一步供气能力 (V1.1 §7.2)。"""
    cur = set(current_on)
    tgt = set(target_on)
    need = required + reserve.extra_m3_min(required)
    starters = [d for d in C.DEVICE_IDS if d in tgt and d not in cur]
    stoppers = [d for d in C.DEVICE_IDS if d in cur and d not in tgt]
    steps: list[dict] = []
    rc: list[str] = []
    live = set(cur)
    # 先启动替代容量
    for d in starters:
        if not caps[d].can_start:
            rc.append(C.RC_MIN_STOP_LOCK)
        live.add(d)
        cap_after = _capacity(caps, tuple(live))
        supply_ok = cap_after + 1e-6 >= need
        steps.append({"step": "start", "device_id": d, "capacity_after_m3min": round(cap_after, 2),
                      "supply_ok": bool(supply_ok), "note": "确认替代容量后启动"})
        if not supply_ok:
            rc.append(C.RC_TRANSITION_CAPACITY_GAP)
    # 再降载/停机原机组
    for d in stoppers:
        if not caps[d].can_stop:
            rc.append(C.RC_MIN_RUN_LOCK)
        live.discard(d)
        cap_after = _capacity(caps, tuple(live))
        supply_ok = cap_after + 1e-6 >= need
        steps.append({"step": "stop", "device_id": d, "capacity_after_m3min": round(cap_after, 2),
                      "supply_ok": bool(supply_ok), "note": "替代容量就绪后停机"})
        if not supply_ok:
            rc.append(C.RC_TRANSITION_CAPACITY_GAP)
    feasible = all(s["supply_ok"] for s in steps) and not (
        C.RC_MIN_RUN_LOCK in rc or C.RC_MIN_STOP_LOCK in rc or C.RC_TRANSITION_CAPACITY_GAP in rc)
    return feasible, steps, _dedup(rc)


def _dedup(seq):
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
