"""MCP 工具与服务测试 (阶段 C, 调度/喘振走 B 接口)。"""
from __future__ import annotations

import asyncio

from air_station.mcp_servers import tools


def test_tool_functions_expose_core_services():
    assert tools.surge_warning()["status"] in ("success", "unknown")
    assert tools.health_diagnose()["status"] == "success"
    assert tools.forecast_load()["status"] == "success"
    assert tools.rul_estimation()["status"] == "success"
    disp = tools.dispatch_optimize(mode="b3")
    assert disp["status"] == "success"
    assert disp["device_actions"]
    assert disp["execution_permission"] == "simulation_candidate_only"
    assert disp["c_meta"]["execution_allowed"] is False


def test_dispatch_modes():
    for mode in ("b3", "balanced", "load_balanced"):
        out = tools.dispatch_optimize(mode=mode, horizon_minutes=15)
        assert out["mode"] == mode
        assert out["c_meta"]["execution_allowed"] is False


def test_orchestrate_tool():
    out = tools.orchestrate("预测未来15分钟负荷", persist=False)
    assert out["status"] == "success"
    assert out["intent"] == "forecast_query"


def test_mcp_server_lists_and_calls():
    from air_station.mcp_servers.server import build_server

    async def _run():
        server = build_server()
        names = {t.name for t in await server.list_tools()}
        assert {"forecast_load", "health_diagnose", "rul_estimation", "dispatch_optimize",
                "surge_warning", "control_command", "orchestrate"} <= names
        res = await server.call_tool("surge_warning", {})
        assert not getattr(res, "isError", False)

    asyncio.run(_run())
