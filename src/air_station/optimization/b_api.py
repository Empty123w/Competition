"""B 侧稳定公共接口 (C/MCP 集成用, B6)。

仅**重导出**已冻结的算法与安全函数并附加版本号, 不改变任何算法逻辑:
  - optimize_dispatch : B3 能效优先 (原任务书要求的直接导入函数)
  - assess_surge_risk  : B4 喘振风险代理评估 (原任务书要求的直接导入函数)
  - plan_dispatch     : 统一多模式调度 (b3 / balanced / load_balanced=LRB)

约定: 配置路径由参数传入 (默认相对项目根); 不读取全局"当前工况"; 返回结构化错误。
所有调度输出 execution_permission="simulation_candidate_only"。
"""
from __future__ import annotations

from air_station.optimization import contracts as C
from air_station.optimization.dispatch_api import MODES as DISPATCH_MODES
from air_station.optimization.dispatch_api import plan_dispatch
from air_station.optimization.service import optimize_dispatch
from air_station.optimization.service import optimize_json as optimize_dispatch_json
from air_station.safety.surge import assess_surge_risk

API_VERSION = "b-api-v1.0"
SUCCESS_STATUS = C.STATUS_SUCCESS
POLICY_VERSIONS = {
    "b3": "b3-optimizer-v0.1",
    "balanced": "balanced-uc-v0.1",
    "load_balanced": "load-balanced-lrb-v0.1",
}


# ------------------------------------------------------------------ #
# 统一状态判定 (不改变任何返回字段/取值; 供 C 直接调用, 避免大小写/语义歧义)
# ------------------------------------------------------------------ #
def is_success(out: dict) -> bool:
    """求解成功且非回退 (不含安全阻断)。status 大小写不敏感。"""
    st = str(out.get("status", "")).lower()
    return (st == SUCCESS_STATUS.lower()) and not bool(out.get("is_fallback"))


def is_fallback_result(out: dict) -> bool:
    """是否为失败后回退到 B1 的结果 (不得视为优化成功)。"""
    return bool(out.get("is_fallback"))


def is_blocked(out: dict) -> bool:
    """是否被 B4 安全阻断 (high/unknown 喘振等), 禁止执行。"""
    return bool(out.get("safety_blocked"))


def is_infeasible(out: dict) -> bool:
    """容量/状态不可行 (无安全自动方案)。status 大小写不敏感。"""
    return str(out.get("status", "")).lower() == C.STATUS_INFEASIBLE.lower()


def candidate_usable(out: dict) -> bool:
    """是否为**可展示的仿真候选**: 成功 & 非回退 & 未阻断。仍非执行许可。"""
    return is_success(out) and not is_blocked(out)


def needs_human_review(out: dict) -> bool:
    """是否需要人工复核 (失败/回退/阻断)。"""
    return (not is_success(out)) or is_blocked(out)


def execution_allowed(out: dict) -> bool:
    """**恒为 False**: B 侧不提供任何真实设备执行许可 (simulation_candidate_only)。"""
    return False


__all__ = [
    "optimize_dispatch",
    "optimize_dispatch_json",
    "assess_surge_risk",
    "plan_dispatch",
    "DISPATCH_MODES",
    "API_VERSION",
    "SUCCESS_STATUS",
    "POLICY_VERSIONS",
    "is_success",
    "is_fallback_result",
    "is_blocked",
    "is_infeasible",
    "candidate_usable",
    "needs_human_review",
    "execution_allowed",
]
