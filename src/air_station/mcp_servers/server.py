"""MCP Server 入口 (阶段 C)。

对外暴露 5 个必备 MCP 服务 + 编排工具, 供 COSMO Code / 天智平台 MCP 框架接入。

启动:
    python -m air_station.mcp_servers.server            # stdio (默认)
    python -m air_station.mcp_servers.server --http      # streamable-http
"""
from __future__ import annotations

import argparse


def build_server():
    from mcp.server.mcpserver import MCPServer

    from air_station.mcp_servers import tools

    server = MCPServer(
        name="air-station-agent",
        instructions="空压站多机协同智能调度 MCP 服务: 负荷预测/健康诊断/调度优化/"
                     "喘振预警/控制指令, 全部基于模拟接口, 不连接真实 PLC。",
        version="0.1.0",
    )

    server.tool(name="forecast_load",
                description="预测未来 15/30/60 分钟用气负荷(点预测+置信区间)")(tools.forecast_load)
    server.tool(name="health_diagnose",
                description="设备健康诊断: 4#/5# 健康评分、风险等级与检修建议")(tools.health_diagnose)
    server.tool(name="rul_estimation",
                description="设备剩余寿命估计(模拟退化模型, validation_mode=simulation)")(tools.rul_estimation)
    server.tool(name="dispatch_optimize",
                description="多机协同调度优化: 开机组合+加载率分配+节能对比")(tools.dispatch_optimize)
    server.tool(name="surge_warning",
                description="离心机喘振风险分级与预警建议")(tools.surge_warning)
    server.tool(name="control_command",
                description="控制指令: SCADA 查询/PLC 下发/方案执行/EAM 维保/工单")(tools.control_command)
    server.tool(name="create_work_order",
                description="生成设备维保工单")(tools.create_work_order)
    server.tool(name="orchestrate",
                description="自然语言编排: 意图理解+工具链调用+可解释方案")(tools.orchestrate)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Air Station MCP Server")
    parser.add_argument("--http", action="store_true", help="使用 streamable-http 传输")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 端口 (仅 --http)")
    args = parser.parse_args(argv)
    server = build_server()
    if args.http:
        server.run("streamable-http")
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
