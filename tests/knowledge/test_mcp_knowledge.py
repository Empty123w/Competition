"""知识库 MCP 工具接入测试。"""
from __future__ import annotations
from air_station.mcp_servers import tools


def test_registered():
    names = [s["name"] for s in tools.TOOL_SPECS]
    assert "knowledge_search" in names


def test_tool_schema():
    out = tools.knowledge_search("负荷预测使用什么模型")
    assert out["status"] in ("success", "no_answer")
    assert {"query", "answer", "citations", "kb_version", "retrieval_mode"} <= set(out.keys())


def test_api_routes():
    from fastapi.testclient import TestClient
    from air_station.api.app import create_app
    c = TestClient(create_app())
    assert c.post("/api/knowledge/ask", json={"question": "负荷预测使用什么模型"}).json()["status"] == "success"
    assert c.post("/api/knowledge/search", json={"query": "健康分数怎么算"}).json()["status"] == "success"
    assert c.get("/api/knowledge/status").json()["index_ok"] is True
