"""控制 / SCADA / EAM / 工单 路由 (阶段 C)。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from air_station.api.errors import ApiError
from air_station.api.schemas import ControlCommandRequest, WorkOrderRequest
from air_station.db import repository
from air_station.mcp_servers import tools

router = APIRouter(tags=["control"])


@router.post("/api/control/command", summary="控制指令(含 SCADA 查询/方案执行/EAM)")
def control_command(req: ControlCommandRequest) -> dict:
    return tools.control_command(
        action=req.action, device_id=req.device_id, action_type=req.action_type,
        target_flow_m3_min=req.target_flow_m3_min, plan=req.plan, reason=req.reason,
        priority=req.priority, minutes=req.minutes, persist=req.persist,
        config_path=req.config_path)


@router.get("/api/control/commands/{command_id}", summary="查询指令执行状态")
def command_status(command_id: str) -> dict:
    cmd = repository.get_command(command_id)
    if cmd is None:
        raise ApiError("E_NOT_FOUND", f"指令不存在: {command_id}")
    return {"status": "success", "command": cmd}


@router.get("/api/scada/realtime", summary="站级实时快照(模拟 SCADA)")
def scada_realtime() -> dict:
    return tools.control_command("scada_query", persist=False)


@router.get("/api/scada/history", summary="单机历史序列")
def scada_history(device_id: str = Query(...), minutes: int = 60) -> dict:
    return tools.control_command("scada_history", device_id=device_id,
                                 minutes=minutes, persist=False)


@router.get("/api/eam/maintenance", summary="维保计划(模拟 EAM)")
def maintenance(device_id: str | None = None) -> dict:
    return tools.control_command("eam_maintenance", device_id=device_id, persist=False)


@router.post("/api/workorders", summary="生成维保工单")
def create_workorder(req: WorkOrderRequest) -> dict:
    return tools.create_work_order(device_id=req.device_id, reason=req.reason,
                                   priority=req.priority)


@router.get("/api/workorders", summary="工单列表")
def list_workorders(limit: int = 50) -> dict:
    return {"status": "success", "work_orders": repository.list_work_orders(limit)}
