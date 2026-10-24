"""维护预警 MCP 工具 (阶段 A7/C 接入)。

封装 A 的 `maintenance_json()`，暴露为 MCP 工具 `maintenance_warning`。
属于**新增**能力，不修改既有工具与其成功字段。
"""
from __future__ import annotations


def maintenance_warning(config_path: str = "configs/maintenance_policy.yaml",
                        processed_dir: str | None = None) -> dict:
    """设备维护预警: 基于真实遥测的振动/温度/电流/能效滚动趋势分数。

    validation_mode = hybrid_simulation（真实遥测作底 + 注入退化标定），
    仅作计划维护软信号, 不触发停机。
    """
    from air_station.models.rul.maintenance_sim import maintenance_json
    return maintenance_json(config_path=config_path, processed_dir=processed_dir)
