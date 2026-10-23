"""MCP 协议级测试 (阶段 C): 以官方客户端通过 stdio 驱动 Server。

覆盖 initialize / tools/list / tools/call, 验证 MCP 端到端可用(非仅进程内注册)。
"""
from __future__ import annotations

import asyncio
import sys

import pytest

pytest.importorskip("mcp")

from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

EXPECTED = {"forecast_load", "dispatch_optimize", "health_diagnose",
            "surge_warning", "control_command"}


def test_mcp_stdio_protocol_roundtrip():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "air_station.mcp_servers.server"])

    async def _run() -> dict:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                res = await session.call_tool("surge_warning", {})
                text = res.content[0].text if res.content else ""
                return {"names": names, "error": bool(getattr(res, "is_error", False)),
                        "text": text}

    out = asyncio.run(_run())
    assert EXPECTED <= out["names"], f"缺必备 MCP 工具: {EXPECTED - out['names']}"
    assert out["error"] is False
    assert '"status"' in out["text"]
