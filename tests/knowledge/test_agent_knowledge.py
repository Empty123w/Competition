"""Agent 知识意图与组合工作流测试。"""
from __future__ import annotations
from air_station.agent import intent as I
from air_station.agent.workflow import run_agent


def test_knowledge_routing_pure():
    r = run_agent("负荷预测模型的输入和输出是什么", persist=False)
    assert r["intent"] == "knowledge_query"
    assert "knowledge_search" in r["tools_used"]


def test_dynamic_still_dynamic():
    r = run_agent("5#现在的健康分数是多少", persist=False)
    assert r["intent"] == "health_query"
    assert "health_diagnose" in r["tools_used"]
    assert "knowledge_search" not in r["tools_used"]


def test_unknown_not_scada():
    r = run_agent("今天天气怎么样", persist=False)
    assert r["intent"] == "unknown"
    assert "scada_query" not in r["tools_used"]
    assert "knowledge_search" in r["tools_used"]


def test_combination_dynamic_plus_knowledge():
    r = run_agent("5#振动偏高请优化调度并解释为什么", persist=False)
    assert "dispatch_optimize" in r["tools_used"]
    assert "knowledge_search" in r["tools_used"]
    assert "补充解释" in r["answer"]
