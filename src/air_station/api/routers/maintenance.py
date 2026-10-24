"""维护预警路由 (阶段 A7/C 接入, 新增)。

GET /api/maintenance/status  -> 设备维护预警(趋势分数)
POST /api/maintenance        -> 同上(便于统一 POST 调用)
不影响既有路由与响应。
"""
from __future__ import annotations

from fastapi import APIRouter

from air_station.mcp_servers import tools

router = APIRouter(tags=["maintenance"])


@router.get("/api/maintenance/status", summary="设备维护预警(趋势分数)")
def status() -> dict:
    return tools.maintenance_warning()


@router.post("/api/maintenance", summary="设备维护预警(POST)")
def maintenance() -> dict:
    return tools.maintenance_warning()
