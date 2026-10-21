"""仓储层测试 (阶段 C)。"""
from __future__ import annotations

from air_station.db import repository


def test_agent_session_roundtrip():
    sid = repository.save_agent_session("优化调度", "dispatch_optimize", "方案...",
                                        ["dispatch_optimize"], {"context": {"flow": 500.0}})
    got = repository.get_agent_session(sid)
    assert got["intent"] == "dispatch_optimize"
    assert got["response"]["context"]["flow"] == 500.0
    assert any(s["id"] == sid for s in repository.list_agent_sessions())


def test_dispatch_plan_and_command_feedback():
    pid = repository.save_dispatch_plan({
        "horizon_minutes": 15,
        "demand": {"flow_m3_min": 500.0, "source": "forecast", "pressure_setpoint_mpa": 0.66},
        "running_count": 2,
        "expected": {"total_power_kw": 2600.0},
        "baseline": {"total_power_kw": 2700.0},
        "comparison": {"power_saving_pct": 3.7},
    })
    plans = repository.list_dispatch_plans()
    assert plans and plans[0]["id"] == pid

    cid = repository.create_control_command("4#", "set_load", 250.0, 0.93, plan_id=pid)
    repository.mark_command(cid, "executed", "ok", feedback={"success": True})
    repository.add_feedback(cid, {"success": True, "actual_flow_m3_min": 250.0,
                                  "actual_power_kw": 1300.0})
    cmd = repository.get_command(cid)
    assert cmd["status"] == "executed"
    assert cmd["plan_id"] == pid
    assert cmd["feedback"]["success"] is True


def test_work_order():
    wid = repository.create_work_order("5#", "排气温度偏高", priority="高", source="agent")
    orders = repository.list_work_orders()
    assert any(o["id"] == wid and o["device_id"] == "5#" for o in orders)
