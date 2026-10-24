"""A7 维护能力在 B/C 的接入测试(工具/适配器/路由)。"""
from __future__ import annotations

import pytest


def test_mcp_tool_registered():
    from air_station.mcp_servers import tools
    names = [s["name"] for s in tools.TOOL_SPECS]
    assert "maintenance_warning" in names
    out = tools.maintenance_warning()
    assert out["status"] == "success"
    assert out["validation_mode"] == "hybrid_simulation"
    assert [d["device_id"] for d in out["devices"]] == ["4#", "5#"]


def test_b_adapter_soft_constraint():
    from air_station.optimization.maintenance_adapter import maintenance_soft_constraint
    s = maintenance_soft_constraint()
    assert s["status"] == "success"
    assert set(s["devices"].keys()) == {"4#", "5#"}
    for d in s["devices"].values():
        assert d["penalty"] == (s["warn_penalty"] if d["warning"] else 0.0)


def test_api_route():
    try:
        from fastapi.testclient import TestClient
    except Exception:
        pytest.skip("fastapi testclient 不可用")
    from air_station.api.app import create_app
    c = TestClient(create_app())
    r = c.get("/api/maintenance/status")
    assert r.status_code == 200
    body = r.json()
    assert body["validation_mode"] == "hybrid_simulation"
    assert len(body["devices"]) == 2
