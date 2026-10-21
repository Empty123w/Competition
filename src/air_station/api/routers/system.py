"""系统与运行总览路由 (阶段 C)。"""
from __future__ import annotations

from fastapi import APIRouter

from air_station.mcp_servers import tools

router = APIRouter(tags=["system"])

_ENDPOINTS = [
    "GET  /api/system/health", "GET  /api/system/overview",
    "POST /api/forecast", "POST /api/health", "POST /api/rul",
    "GET  /api/health/rul", "POST /api/dispatch", "POST /api/surge",
    "POST /api/control/command", "GET  /api/scada/realtime",
    "GET  /api/eam/maintenance", "POST /api/workorders", "GET /api/workorders",
    "POST /api/agent/chat", "GET  /api/agent/sessions",
    "GET  /api/metrics/comparison", "MCP  /mcp (streamable-http)",
]


@router.get("/", summary="服务首页/能力清单")
def root() -> dict:
    return {"service": "空压站多机协同智能调度 Agent",
            "module": "C 后端 / MCP / Agent",
            "status": "ok",
            "docs": "/docs",
            "endpoints": _ENDPOINTS,
            "safety": "仅使用模拟 PLC/SCADA/EAM, 不连接真实设备"}


@router.get("/api/system/health", summary="存活探针")
def health() -> dict:
    return {"status": "success", "service": "air-station-agent", "uptime": "ok"}


@router.get("/api/system/info", summary="服务信息")
def info() -> dict:
    return {"status": "success", "module": "C",
            "capabilities": ["forecast", "health", "rul", "dispatch", "surge",
                             "control", "agent"],
            "mcp_tools": ["forecast_load", "health_diagnose", "rul_estimation",
                          "dispatch_optimize", "surge_warning", "control_command",
                          "create_work_order", "orchestrate"]}


@router.get("/api/system/overview", summary="运行总览")
def overview() -> dict:
    scada = tools.control_command("scada_query", persist=False)
    health = tools.health_diagnose()
    rul = tools.rul_estimation()
    surge = tools.surge_warning()
    running = [d["device_id"] for d in scada.get("devices", []) if d.get("running")]
    return {
        "status": "success",
        "scada": scada,
        "health": health,
        "rul": rul,
        "surge": surge,
        "running_devices": running,
    }
