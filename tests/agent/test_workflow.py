"""Agent 工作流测试 (阶段 C, 调度走 B 接口)。"""
from __future__ import annotations

from air_station.agent import intent
from air_station.agent.workflow import run_agent


def test_intent_extractors():
    assert intent.extract_device("4号机组振动偏高") == "4#"
    assert intent.extract_device("5# 排气温度") == "5#"
    assert intent.extract_horizon("未来30分钟的负荷") == 30
    assert intent.extract_flow("需求 420 m3/min") == 420.0


def test_intent_classification():
    assert intent.classify("请优化调度")[0] == "dispatch_optimize"
    assert intent.classify("预测未来负荷")[0] == "forecast_query"
    assert intent.classify("设备健康诊断")[0] == "health_query"
    assert intent.classify("喘振风险如何")[0] == "surge_query"
    assert intent.classify("剩余寿命还有多久")[0] == "rul_query"
    assert intent.classify("生成维保工单")[0] == "workorder"
    assert intent.classify("你好呀")[0] == "unknown"


def test_run_agent_dispatch_explains():
    out = run_agent("3号机组振动偏高，请优化调度", persist=False)
    assert out["status"] == "success"
    assert out["intent"] == "dispatch_optimize"
    assert out["plan"] is not None
    assert out["plan"]["execution_permission"] == "simulation_candidate_only"
    assert out["answer"]
    assert out["explain_chain"]
    assert "dispatch_optimize" in out["tools_used"]


def test_run_agent_rul_query():
    out = run_agent("5号机组剩余寿命还有多久", persist=False)
    assert out["intent"] == "rul_query"
    assert "rul_estimation" in out["tools_used"]
    assert "模拟" in out["answer"]


def test_run_agent_control_execute_gate_and_feedback():
    out = run_agent("优化调度并执行", execute=True, persist=False)
    ctl = out["tool_results"].get("control_command", {})
    assert ctl.get("status") == "success"
    # B 正常方案 -> 准入通过并在模拟环境执行
    assert ctl.get("accepted") is True
    assert ctl.get("requires_human_review") is True


def test_run_agent_persists_and_multiturn():
    out = run_agent("优化调度", persist=True)
    sid = out["session_id"]
    assert sid
    from air_station.db import repository
    assert repository.get_agent_session(sid) is not None
    out2 = run_agent("执行该方案", execute=True, persist=True, session_id=sid)
    assert out2["intent"] == "control_execute"
    assert out2["tool_results"].get("control_command", {}).get("status") != "error"


def test_run_agent_unknown_fallback():
    out = run_agent("天气不错", persist=False)
    assert out["intent"] == "unknown"
    assert out["answer"]
