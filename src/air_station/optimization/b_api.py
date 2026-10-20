"""B 侧稳定公共接口 (C/MCP 集成用, B6)。

仅**重导出**已冻结的算法与安全函数并附加版本号, 不改变任何算法逻辑:
  - optimize_dispatch : B3 能效优先 (原任务书要求的直接导入函数)
  - assess_surge_risk  : B4 喘振风险代理评估 (原任务书要求的直接导入函数)
  - plan_dispatch     : 统一多模式调度 (b3 / balanced / load_balanced=LRB)

约定: 配置路径由参数传入 (默认相对项目根); 不读取全局"当前工况"; 返回结构化错误。
所有调度输出 execution_permission="simulation_candidate_only"。
"""
from __future__ import annotations

from air_station.optimization.dispatch_api import MODES as DISPATCH_MODES
from air_station.optimization.dispatch_api import plan_dispatch
from air_station.optimization.service import optimize_dispatch
from air_station.optimization.service import optimize_json as optimize_dispatch_json
from air_station.safety.surge import assess_surge_risk

API_VERSION = "b-api-v1.0"
POLICY_VERSIONS = {
    "b3": "b3-optimizer-v0.1",
    "balanced": "balanced-uc-v0.1",
    "load_balanced": "load-balanced-lrb-v0.1",
}

__all__ = [
    "optimize_dispatch",
    "optimize_dispatch_json",
    "assess_surge_risk",
    "plan_dispatch",
    "DISPATCH_MODES",
    "API_VERSION",
    "POLICY_VERSIONS",
]
