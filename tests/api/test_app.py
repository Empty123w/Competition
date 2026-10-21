"""API 端到端测试 (阶段 C): 端点、统一错误、B 调度与模拟控制。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from air_station.api.app import app
    from air_station.control import simulator
    simulator.reset()
    return TestClient(app)


def test_root_and_system(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert client.get("/api/system/health").json()["status"] == "success"
    ov = client.get("/api/system/overview").json()
    assert "scada" in ov and "health" in ov and "rul" in ov and "surge" in ov


def test_model_endpoints(client):
    assert client.post("/api/forecast", json={}).json()["status"] == "success"
    assert client.post("/api/health", json={}).json()["status"] == "success"
    assert client.post("/api/rul", json={}).json()["status"] == "success"
    assert client.get("/api/health/rul").json()["status"] == "success"
    assert client.post("/api/surge", json={}).json()["status"] in ("success", "unknown")
    disp = client.post("/api/dispatch", json={"mode": "b3", "horizon_minutes": 15,
                                              "persist": True}).json()
    assert disp["status"] == "success"
    assert disp["execution_permission"] == "simulation_candidate_only"
    assert disp.get("plan_id")
    assert client.get("/api/dispatch/plans").json()["plans"]


def test_control_endpoints(client):
    snap = client.get("/api/scada/realtime").json()
    assert snap["source"] == "simulated_scada"
    cmd = client.post("/api/control/command", json={
        "action": "plc_command", "device_id": "4#", "action_type": "set_load",
        "target_flow_m3_min": 240.0, "persist": True}).json()
    assert cmd["accepted"] is True
    cid = cmd["command_id"]
    assert client.get(f"/api/control/commands/{cid}").json()["command"]["status"] == "executed"

    plan = client.post("/api/dispatch", json={"mode": "b3", "persist": True}).json()
    ex = client.post("/api/control/command", json={
        "action": "execute_plan", "plan": plan, "persist": True}).json()
    assert ex["accepted"] is True
    assert ex["command_count"] >= 0

    wo = client.post("/api/workorders", json={"device_id": "5#", "priority": "高"}).json()
    assert wo["work_order_id"]
    assert client.get("/api/workorders").json()["work_orders"]
    assert client.get("/api/eam/maintenance").json()["status"] == "success"


def test_agent_endpoints(client):
    r = client.post("/api/agent/chat", json={"message": "预测未来负荷"})
    j = r.json()
    assert j["status"] == "success" and j["intent"] == "forecast_query"
    sid = j["session_id"]
    assert client.get("/api/agent/sessions").json()["sessions"]
    assert client.get(f"/api/agent/sessions/{sid}").json()["session"]["id"] == sid


def test_unified_errors(client):
    r = client.get("/api/agent/sessions/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "E_NOT_FOUND"
    assert body["error"]["request_id"]

    r = client.post("/api/workorders", json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "E_VALIDATION"


def test_metrics_comparison(client):
    client.post("/api/dispatch", json={"mode": "b3", "persist": True})
    j = client.get("/api/metrics/comparison").json()
    assert j["status"] == "success"
    assert j["latest_dispatch"] is not None
