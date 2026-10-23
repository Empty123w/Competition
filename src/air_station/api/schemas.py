"""API 请求模型 (阶段 C)。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ForecastRequest(BaseModel):
    horizons: list[int] | None = Field(default=None, description="预测时域(分钟), 默认 15/30/60")


class DispatchRequest(BaseModel):
    mode: str = Field(default="b3", description="调度模式: b3|balanced|load_balanced")
    demand_flow_m3_min: float | None = Field(default=None, description="需求流量覆盖(缺省用预测)")
    horizon_minutes: int = Field(default=15, description="调度时域(分钟)")
    timeout_ms: float | None = Field(default=None, description="求解超时(ms), <=0 触发回退")
    optimization_config: str = "configs/optimization.yaml"
    persist: bool = Field(default=False, description="是否落库调度方案")


class ControlCommandRequest(BaseModel):
    action: str = Field(description="scada_query|scada_history|plc_command|execute_plan|"
                                    "eam_maintenance|work_order|command_status")
    device_id: str | None = None
    action_type: str | None = Field(default=None, description="run|stop|set_load")
    target_flow_m3_min: float | None = None
    plan: dict | None = None
    reason: str | None = None
    priority: str = "中"
    minutes: int = 60
    persist: bool = True
    config_path: str = "configs/control.yaml"


class WorkOrderRequest(BaseModel):
    device_id: str
    reason: str | None = None
    priority: str = "中"


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1)
    execute: bool = Field(default=False, description="是否真正下发控制指令(模拟)")
    session_id: str | None = None
    persist: bool = True
