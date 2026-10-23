"""C → B 集成适配层 (阶段 C)。

职责: 把 C 侧服务(A 的预测/健康、设备状态、运行配置)组装成 B 的输入契约,
调用 B 的稳定接口 `air_station.optimization.b_api`, 并把 B 的输出原样对外,
附加少量 C 侧元信息(c_meta)。C 不复制、不修改 B 的算法与字段。
"""
from air_station.integration.dispatch_bridge import (  # noqa: F401
    build_constraints,
    build_forecast,
    build_health,
    build_snapshot,
    dispatch_plan,
)
from air_station.integration.surge_bridge import surge_assessment  # noqa: F401

__all__ = ["dispatch_plan", "surge_assessment", "build_snapshot", "build_forecast",
           "build_health", "build_constraints"]
