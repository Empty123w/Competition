"""控制闭环测试 (阶段 C): 安全联锁、指令下发、B 方案准入、工单。"""
from __future__ import annotations

from air_station.control import interlock, simulator
from air_station.control.service import control_json, execute_plan, load_config

RATED = 268.9


def _cfg_for_test():
    cfg = load_config()
    cfg.setdefault("simulation", {})
    cfg["simulation"]["latency_ms"] = 0
    cfg["simulation"]["feedback_delay_ms"] = 0
    return cfg


def _ok_plan() -> dict:
    return {
        "status": "success", "mode": "b3", "is_fallback": False,
        "execution_permission": "simulation_candidate_only", "safety_blocked": False,
        "target_combination": ["4#", "5#"],
        "device_actions": [
            {"device_id": "4#", "action": "adjust", "target_flow_m3_min": 240.0,
             "running_after": True},
            {"device_id": "5#", "action": "adjust", "target_flow_m3_min": 240.0,
             "running_after": True},
        ],
        "c_meta": {"candidate_usable": True, "needs_human_review": False,
                   "is_success": True, "is_fallback": False, "is_blocked": False,
                   "is_infeasible": False, "execution_allowed": False},
    }


def test_interlock_rejects_flow_out_of_range(sim_reset):
    cfg = _cfg_for_test()
    snap = simulator.snapshot()
    ok, reason = interlock.check_command("4#", "set_load", 9999.0, RATED, snap, cfg)
    assert not ok and "超出" in reason


def test_interlock_rejects_last_unit_stop(sim_reset):
    cfg = _cfg_for_test()
    control_json("plc_command", {"device_id": "5#", "action_type": "stop"},
                 persist=False, config_path="configs/control.yaml")
    snap = simulator.snapshot()
    ok, reason = interlock.check_command("4#", "stop", None, RATED, snap, cfg)
    assert not ok and "最小要求" in reason


def test_plc_command_feedback(sim_reset):
    out = control_json("plc_command",
                       {"device_id": "4#", "action_type": "set_load",
                        "target_flow_m3_min": 250.0}, persist=True)
    assert out["accepted"] is True
    assert out["command_status"] == "executed"
    assert out["feedback"]["actual_flow_m3_min"] == 250.0
    assert out["command_id"]


def test_execute_plan_closed_loop(sim_reset):
    cfg = _cfg_for_test()
    out = execute_plan(cfg, _ok_plan(), persist=True)
    assert out["status"] == "success"
    assert out["accepted"] is True
    assert out["command_count"] == 2
    assert all(r["accepted"] for r in out["results"])
    assert out["actual_total_power_kw"] > 0


def test_execute_plan_gate_rejects_blocked(sim_reset):
    cfg = _cfg_for_test()
    blocked = _ok_plan()
    blocked["safety_blocked"] = True
    blocked["c_meta"]["is_blocked"] = True
    out = execute_plan(cfg, blocked, persist=False)
    assert out["accepted"] is False
    assert out["command_count"] == 0
    assert "安全阻断" in out["rejected_reason"]


def test_execute_plan_gate_rejects_fallback(sim_reset):
    cfg = _cfg_for_test()
    fb = _ok_plan()
    fb["is_fallback"] = True
    fb["c_meta"]["is_fallback"] = True
    out = execute_plan(cfg, fb, persist=False)
    assert out["accepted"] is False
    assert "人工复核" in out["rejected_reason"]


def test_scada_query_and_history(sim_reset):
    snap = control_json("scada_query", persist=False)
    assert snap["source"] == "simulated_scada"
    assert len(snap["devices"]) == 2
    hist = control_json("scada_history", {"device_id": "4#", "minutes": 30}, persist=False)
    assert hist["records"]


def test_work_order_persisted(sim_reset):
    out = control_json("work_order", {"device_id": "4#", "priority": "高"}, persist=True)
    assert out["status"] == "success"
    assert out["work_order_id"]
    from air_station.db import repository
    orders = repository.list_work_orders()
    assert any(o["id"] == out["work_order_id"] for o in orders)


def test_unknown_action_raises(sim_reset):
    import pytest
    with pytest.raises(ValueError):
        control_json("nope", {}, persist=False)
