"""B 模块公共契约 (B1 最小子集, 供后续 B3 复用)。

本模块**只定义数据结构与常量**, 不含调度/优化逻辑。
字段命名最终以 C 维护的公共契约为准 (任务书 V1.1 §3.3)。
B1 实现只使用其中与人工规则基线相关的部分。

单位约定:
  flow_m3_min        -> m3/min
  power_kw           -> kW
  *_pressure_mpa     -> MPa(表压)
  igv_percent        -> %
  bov_closed_percent -> %(闭度, 100=全关)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

# ------------------------------------------------------------------ #
# 状态与原因码 (结构化返回, 供 C 区分处理)
# ------------------------------------------------------------------ #
STATUS_SUCCESS = "success"
STATUS_INFEASIBLE = "INFEASIBLE"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_INVALID_INPUT = "INVALID_INPUT"
STATUS_TIMEOUT = "TIMEOUT"

RC_INVALID_INPUT = "INVALID_INPUT"
RC_STALE_FORECAST = "STALE_FORECAST"
RC_INFEASIBLE_CAPACITY = "INFEASIBLE_CAPACITY"
RC_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
RC_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
RC_FORECAST_MISSING_HOLD = "FORECAST_MISSING_HOLD"
RC_HEALTH_DERATE = "HEALTH_DERATE"
RC_HEALTH_STOP_REVIEW = "HEALTH_STOP_REVIEW"
RC_RESERVE_VIOLATION = "RESERVE_VIOLATION"
RC_MIN_RUN_LOCK = "MIN_RUN_LOCK"
RC_MIN_STOP_LOCK = "MIN_STOP_LOCK"
RC_MAX_SWITCH_LOCK = "MAX_SWITCH_LOCK"
RC_PRESSURE_HARD_LOW = "PRESSURE_HARD_LOW"
RC_PRESSURE_LOW_START = "PRESSURE_LOW_START"
RC_PRESSURE_HIGH_STOP = "PRESSURE_HIGH_STOP"
RC_CAPACITY_START = "CAPACITY_LOW_START"
RC_ADJUST_LOAD = "ADJUST_LOAD"
RC_HOLD = "HOLD"
RC_DEBOUNCE_INSUFFICIENT = "DEBOUNCE_INSUFFICIENT_HISTORY"
# --- B3 ---
RC_TIMEOUT = "TIMEOUT"
RC_FALLBACK_RULE = "FALLBACK_RULE"
RC_DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
RC_RESERVE_N1 = "RESERVE_N1_INSUFFICIENT"
RC_TRANSITION_CAPACITY_GAP = "TRANSITION_CAPACITY_GAP"
RC_OUT_OF_MODEL_RANGE = "OUT_OF_MODEL_RANGE"
RC_POWER_OVER_CAP = "POWER_OVER_CAP"
RC_SURGE_NOT_ASSESSED = "SURGE_NOT_ASSESSED_B4"
RC_PRESSURE_COUNTERFACTUAL = "PRESSURE_COUNTERFACTUAL_UNSUPPORTED"
RC_RUL_SIMULATION = "RUL_SIMULATION_PENALTY_ON"

# 真实设备仅 4#、5# (V1.1 §2.2); 3# 仅用于独立仿真场景
DEVICE_IDS = ("4#", "5#")

# 内部风险枚举
RISK_LEVELS = ("normal", "attention", "warning", "critical")
_CN_RISK = {"正常": "normal", "关注": "attention", "预警": "warning", "严重": "critical"}

# 视为"未冻结/非实测"的来源标记, 会触发 validation_mode=simulation
UNCONFIRMED_SOURCES = {"pending", "engineering", "simulation", "unknown"}


# ------------------------------------------------------------------ #
# 数值工具: 保证 JSON 安全 (无 NaN / Infinity)
# ------------------------------------------------------------------ #
def is_finite_number(x: Any) -> bool:
    if x is None:
        return False
    if isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def safe_float(x: Any, digits: Optional[int] = None) -> Optional[float]:
    if not is_finite_number(x):
        return None
    v = float(x)
    return round(v, digits) if digits is not None else v


def normalize_device_id(value: Any) -> str:
    """接受 '4#' / '4' / 4 -> '4#'; 其余原样返回(由调用方判非法)。"""
    s = str(value).strip()
    if s.endswith("#"):
        s = s[:-1]
    if s in ("4", "4.0"):
        return "4#"
    if s in ("5", "5.0"):
        return "5#"
    return str(value)


def device_number(device_id: str) -> Optional[int]:
    did = normalize_device_id(device_id)
    if did == "4#":
        return 4
    if did == "5#":
        return 5
    return None


# ------------------------------------------------------------------ #
# 输入契约
# ------------------------------------------------------------------ #
@dataclass
class DeviceSnapshot:
    device_id: str
    running: bool
    flow_m3_min: Optional[float] = None
    power_kw: Optional[float] = None
    discharge_pressure_mpa: Optional[float] = None
    igv_percent: Optional[float] = None
    bov_closed_percent: Optional[float] = None
    quality_valid: bool = True
    data_source: str = "unknown"

    def __post_init__(self) -> None:
        self.device_id = normalize_device_id(self.device_id)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "running": bool(self.running),
            "flow_m3_min": safe_float(self.flow_m3_min, 2),
            "power_kw": safe_float(self.power_kw, 2),
            "discharge_pressure_mpa": safe_float(self.discharge_pressure_mpa, 4),
            "igv_percent": safe_float(self.igv_percent, 1),
            "bov_closed_percent": safe_float(self.bov_closed_percent, 1),
            "quality_valid": bool(self.quality_valid),
            "data_source": self.data_source,
        }


@dataclass
class StationSnapshot:
    as_of: str
    header_pressure_mpa: Optional[float]
    devices: list[DeviceSnapshot] = field(default_factory=list)
    data_source: str = "unknown"

    def device_map(self) -> dict[str, DeviceSnapshot]:
        return {d.device_id: d for d in self.devices}

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "header_pressure_mpa": safe_float(self.header_pressure_mpa, 4),
            "devices": [d.to_dict() for d in self.devices],
            "data_source": self.data_source,
        }


@dataclass
class ForecastResult:
    as_of: str
    horizon_minutes: int
    predicted_flow: Optional[float]
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    forecast_id: Optional[str] = None

    def demand_value(self, basis: str = "upper_bound") -> Optional[float]:
        """按口径取需求值; CAL-02 默认 upper_bound。"""
        candidates = {
            "predicted_flow": self.predicted_flow,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
        }
        return safe_float(candidates.get(basis))

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "horizon_minutes": int(self.horizon_minutes),
            "predicted_flow": safe_float(self.predicted_flow, 2),
            "lower_bound": safe_float(self.lower_bound, 2),
            "upper_bound": safe_float(self.upper_bound, 2),
            "forecast_id": self.forecast_id,
        }


@dataclass
class HealthAssessment:
    device_id: str
    risk_level: str
    health_score: Optional[float] = None
    suggestion: str = ""
    source: str = "unsupervised"

    def __post_init__(self) -> None:
        self.device_id = normalize_device_id(self.device_id)

    @property
    def internal_risk(self) -> str:
        """中文风险等级 -> 内部枚举; 未知按 attention 处理(保守但不自动停机)。"""
        raw = str(self.risk_level).strip()
        if raw in _CN_RISK:
            return _CN_RISK[raw]
        if raw in RISK_LEVELS:
            return raw
        return "attention"

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "risk_level": self.risk_level,
            "internal_risk": self.internal_risk,
            "health_score": safe_float(self.health_score),
            "suggestion": self.suggestion,
            "source": self.source,
        }


@dataclass
class ReservePolicy:
    """CAL-03: 备用容量的类型和值分别配置。"""

    mode: str = "extra"          # extra | n_minus_one | none
    value: float = 0.05
    unit: str = "percent"        # percent | m3_min
    source: str = "pending"

    def extra_m3_min(self, required_flow: Optional[float]) -> float:
        if self.mode == "none" or required_flow is None:
            return 0.0
        if self.mode != "extra":
            # n_minus_one 不折算为额外流量, 由规则另做单机失效校验
            return 0.0
        if self.unit == "percent":
            return float(required_flow) * float(self.value)
        return float(self.value)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "value": safe_float(self.value), "unit": self.unit,
                "source": self.source}


@dataclass
class DispatchConstraints:
    """从配置构建的运行约束 (CAL-01~06)。所有阈值外部化, 代码不硬编码。"""

    pressure_band_mpa: tuple[float, float] = (0.55, 0.66)
    pressure_hard_lo_mpa: float = 0.50
    pressure_setpoint_mpa: float = 0.60
    confirm_minutes: int = 3
    hysteresis_mpa: float = 0.02
    reserve: ReservePolicy = field(default_factory=ReservePolicy)
    demand_basis: str = "upper_bound"
    demand_horizon_minutes: int = 60
    min_run_minutes: int = 15
    min_stop_minutes: int = 15
    max_switches_per_hour: int = 2
    rated_capacity_m3min: float = 268.9
    validated_min_m3min: float = 120.0
    validated_operating_max_m3min: float = 268.9
    rated_power_kw: float = 1250.0
    health_mode: str = "observe"       # observe | derate
    health_status: str = "pending"
    health_derate_map: dict = field(default_factory=lambda: {
        "normal": 1.0, "attention": 1.0, "warning": 1.0, "critical": 1.0})
    max_forecast_age_minutes: int = 15
    start_priority: tuple[str, ...] = ("5#", "4#")
    stop_priority: tuple[str, ...] = ("4#", "5#")
    load_ratio_target: Optional[float] = None
    load_ratio_tolerance: Optional[float] = None
    source_flags: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict) -> "DispatchConstraints":
        pressure = cfg.get("pressure", {})
        band = pressure.get("band_mpa", [0.55, 0.66])
        reserve = cfg.get("reserve", {})
        cap = cfg.get("capacity", {})
        sw = cfg.get("switching", {})
        health = cfg.get("health", {})
        demand = cfg.get("demand", {})
        prio = cfg.get("priority", {})
        lr = cfg.get("load_ratio", {})
        time_cfg = cfg.get("time", {})
        cap_src = cap.get("source_flags", {})
        flags = {
            "pressure_band": pressure.get("source", "pending"),
            "reserve": reserve.get("source", "pending"),
            "demand_basis": demand.get("source", "pending"),
            "min_run_minutes": sw.get("source", "engineering"),
            "min_stop_minutes": sw.get("source", "engineering"),
            "health_derate": health.get("source", "pending"),
            "validated_min_m3min": cap_src.get("validated_min", "engineering"),
            "validated_operating_max_m3min": cap_src.get("validated_operating_max", "engineering"),
            "rated_capacity_m3min": cap_src.get("rated", "real"),
        }
        return cls(
            pressure_band_mpa=(float(band[0]), float(band[1])),
            pressure_hard_lo_mpa=float(pressure.get("hard_lo_mpa", 0.50)),
            pressure_setpoint_mpa=float(pressure.get("setpoint_mpa", 0.60)),
            confirm_minutes=int(pressure.get("confirm_minutes", 3)),
            hysteresis_mpa=float(pressure.get("hysteresis_mpa", 0.02)),
            reserve=ReservePolicy(
                mode=reserve.get("mode", "extra"),
                value=float(reserve.get("value", 0.0)),
                unit=reserve.get("unit", "percent"),
                source=reserve.get("source", "pending"),
            ),
            demand_basis=demand.get("basis", "upper_bound"),
            demand_horizon_minutes=int(demand.get("horizon_minutes", 60)),
            min_run_minutes=int(sw.get("min_run_minutes", 15)),
            min_stop_minutes=int(sw.get("min_stop_minutes", 15)),
            max_switches_per_hour=int(sw.get("max_switches_per_hour", 2)),
            rated_capacity_m3min=float(cap.get("rated_capacity_m3min", 268.9)),
            validated_min_m3min=float(cap.get("validated_min_m3min", 120.0)),
            validated_operating_max_m3min=float(cap.get("validated_operating_max_m3min", 268.9)),
            rated_power_kw=float(cfg.get("constraints", {}).get("rated_power_kw", 1250.0)),
            health_mode=health.get("mode", "observe"),
            health_status=health.get("status", "pending"),
            health_derate_map=dict(health.get("derate_map", {
                "normal": 1.0, "attention": 1.0, "warning": 1.0, "critical": 1.0})),
            max_forecast_age_minutes=int(time_cfg.get("max_forecast_age_minutes", 15)),
            start_priority=tuple(normalize_device_id(x) for x in prio.get("start_priority", ["5#", "4#"])),
            stop_priority=tuple(normalize_device_id(x) for x in prio.get("stop_priority", ["4#", "5#"])),
            load_ratio_target=lr.get("target"),
            load_ratio_tolerance=lr.get("tolerance"),
            source_flags=flags,
        )

    def applied_validation_mode(self) -> str:
        """任一被使用的参数来源未确认 -> simulation; 否则 rule_baseline。"""
        used = [self.source_flags.get(k, "unknown") for k in self.source_flags]
        used += [self.reserve.source, self.health_status]
        return "simulation" if any(s in UNCONFIRMED_SOURCES for s in used) else "rule_baseline"


@dataclass
class RulAssessment:
    """B3 可选模拟寿命输入 (来自 A 的 rul_json(), validation_mode=simulation)。

    仅参与**软惩罚排序**, 不触发停机; 缺失时不得补造。
    """
    device_id: str
    estimated_rul_hours: Optional[float] = None
    lower_bound_hours: Optional[float] = None
    upper_bound_hours: Optional[float] = None
    method: str = ""
    source: str = "simulation"
    validation_mode: str = "simulation"

    def __post_init__(self) -> None:
        self.device_id = normalize_device_id(self.device_id)

    @classmethod
    def from_rul_json(cls, payload: dict) -> list["RulAssessment"]:
        out = []
        for d in (payload or {}).get("devices", []):
            out.append(cls(
                device_id=d.get("device_id"),
                estimated_rul_hours=safe_float(d.get("estimated_rul_hours")),
                lower_bound_hours=safe_float(d.get("lower_bound_hours")),
                upper_bound_hours=safe_float(d.get("upper_bound_hours")),
                method=d.get("method", ""),
                source="simulation", validation_mode=(payload or {}).get("validation_mode", "simulation"),
            ))
        return out

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "estimated_rul_hours": safe_float(self.estimated_rul_hours, 1),
            "lower_bound_hours": safe_float(self.lower_bound_hours, 1),
            "upper_bound_hours": safe_float(self.upper_bound_hours, 1),
            "method": self.method,
            "source": self.source,
            "validation_mode": self.validation_mode,
        }


# ------------------------------------------------------------------ #
# 规则状态 (供最小运行/停机与去抖判定; 由 pipeline 跨步传递)
# ------------------------------------------------------------------ #
@dataclass
class PolicyState:
    last_switch_at: dict[str, str] = field(default_factory=dict)
    pressure_history: list[tuple[str, float]] = field(default_factory=list)
    recent_switch_times: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# 输出契约
# ------------------------------------------------------------------ #
@dataclass
class DeviceAction:
    device_id: str
    action: str                        # start | stop | hold | adjust
    target_flow_m3_min: Optional[float]
    target_load_ratio: Optional[float]
    running_after: bool
    estimated_power_kw: Optional[float] = None
    reason_codes: list[str] = field(default_factory=list)
    upper_limit_m3_min: Optional[float] = None
    health_factor: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "action": self.action,
            "target_flow_m3_min": safe_float(self.target_flow_m3_min, 2),
            "target_load_ratio": safe_float(self.target_load_ratio, 4),
            "running_after": bool(self.running_after),
            "estimated_power_kw": safe_float(self.estimated_power_kw, 2),
            "reason_codes": list(self.reason_codes),
            "upper_limit_m3_min": safe_float(self.upper_limit_m3_min, 2),
            "health_factor": safe_float(self.health_factor, 3),
        }


@dataclass
class ConstraintCheck:
    name: str
    passed: bool
    margin: Optional[float] = None
    unit: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "pass": bool(self.passed),
                "margin": safe_float(self.margin, 3), "unit": self.unit, "detail": self.detail}


@dataclass
class DispatchResult:
    status: str
    as_of: str
    policy_version: str
    validation_mode: str = "simulation"
    reason_codes: list[str] = field(default_factory=list)
    forecast_id: Optional[str] = None
    health_ids: list[str] = field(default_factory=list)
    model_version: Optional[str] = None
    device_actions: list[DeviceAction] = field(default_factory=list)
    total_flow_m3_min: Optional[float] = None
    required_flow_m3_min: Optional[float] = None
    estimated_power_kw: Optional[float] = None
    pressure_setpoint_mpa: Optional[float] = None
    reserve_m3_min: Optional[float] = None
    constraints: list[ConstraintCheck] = field(default_factory=list)
    objective_components: dict = field(default_factory=dict)
    explanation: str = ""
    elapsed_seconds: Optional[float] = None
    action_sequence: list[str] = field(default_factory=list)
    source_flags: dict = field(default_factory=dict)
    # --- B3 扩展字段 (向后兼容, 默认空/None) ---
    target_combination: list[str] = field(default_factory=list)
    estimated_energy_kwh: Optional[float] = None
    pressure_prediction: dict = field(default_factory=dict)
    surge_status: str = "not_assessed_b4"
    transition_plan: list[dict] = field(default_factory=list)
    baseline_comparison: dict = field(default_factory=dict)
    execution_permission: str = "simulation_candidate_only"
    simulation_feasible: bool = False
    requires_field_review: bool = True
    fallback_candidate: Optional[dict] = None
    is_fallback: bool = False

    @property
    def feasible(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "as_of": self.as_of,
            "forecast_id": self.forecast_id,
            "health_ids": list(self.health_ids),
            "model_version": self.model_version,
            "policy_version": self.policy_version,
            "validation_mode": self.validation_mode,
            "objective_components": _json_safe(self.objective_components),
            "device_actions": [a.to_dict() for a in self.device_actions],
            "total_flow_m3_min": safe_float(self.total_flow_m3_min, 2),
            "required_flow_m3_min": safe_float(self.required_flow_m3_min, 2),
            "estimated_power_kw": safe_float(self.estimated_power_kw, 2),
            "pressure_setpoint_mpa": safe_float(self.pressure_setpoint_mpa, 4),
            "reserve_m3_min": safe_float(self.reserve_m3_min, 2),
            "constraints": [c.to_dict() for c in self.constraints],
            "explanation": self.explanation,
            "elapsed_seconds": safe_float(self.elapsed_seconds, 4),
            "action_sequence": list(self.action_sequence),
            "source_flags": dict(self.source_flags),
            "target_combination": list(self.target_combination),
            "estimated_energy_kwh": safe_float(self.estimated_energy_kwh, 2),
            "pressure_prediction": _json_safe(self.pressure_prediction),
            "surge_status": self.surge_status,
            "transition_plan": _json_safe(self.transition_plan),
            "baseline_comparison": _json_safe(self.baseline_comparison),
            "execution_permission": self.execution_permission,
            "simulation_feasible": bool(self.simulation_feasible),
            "requires_field_review": bool(self.requires_field_review),
            "fallback_candidate": _json_safe(self.fallback_candidate) if self.fallback_candidate else None,
            "is_fallback": bool(self.is_fallback),
        }


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return safe_float(obj, 4)
    return obj
