"""B5 连续状态回放语义测试。

覆盖: 可执行判定、不可评估步冻结模拟状态但锁定时钟继续推进、_apply 仅在可执行时推进。
"""
from __future__ import annotations

import pandas as pd

import b5_backtest as B  # 由 conftest 注入 analysis/scripts 路径
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization.contracts import (
    DeviceAction,
    DeviceSnapshot,
    DispatchResult,
    PolicyState,
    StationSnapshot,
)


def _cand(status="success", rc=None, blocked=False, actions=None, transition=None):
    return DispatchResult(status=status, as_of="2026-09-01T00:00:00", policy_version="t",
                          reason_codes=list(rc or []), safety_blocked=blocked,
                          device_actions=actions or [], transition_plan=transition or [])


def test_executable_rules():
    assert B.executable(_cand()) is True
    assert B.executable(_cand(blocked=True)) is False
    assert B.executable(_cand(status="INFEASIBLE")) is False
    assert B.executable(_cand(rc=[C.RC_TRANSITION_CAPACITY_GAP])) is False
    assert B.executable(_cand(transition=[{"step": "stop", "supply_ok": False}])) is False


def test_lock_timer_advances_with_time_while_state_frozen():
    """不可评估步不推进模拟状态, 但锁定时钟按真实时间前进。"""
    con = C.DispatchConstraints.from_config(
        {"switching": {"min_run_minutes": 15, "min_stop_minutes": 15, "max_switches_per_hour": 2}})
    snap = StationSnapshot("2026-09-01T00:00:00", 0.60,
                           [DeviceSnapshot("4#", False, 0.0, 0.0, 0.58, 100, 100),
                            DeviceSnapshot("5#", True, 240.0, 1280.0, 0.60, 100, 100)])
    state = PolicyState(last_switch_at={"4#": "2026-09-01T00:00:00"})   # 4# 刚停机
    # +5 min: 未到 min_stop -> 不可启动
    c5 = CONS.build_capabilities(con, snap, [], state)
    assert c5["4#"].can_start is False
    # 直接推进时钟到 +20 min (模拟多个不可评估步过后), 仍用同一冻结状态
    snap2 = StationSnapshot("2026-09-01T00:20:00", 0.60, snap.devices)
    c20 = CONS.build_capabilities(con, snap2, [], state)
    assert c20["4#"].can_start is True     # 锁定时钟已前进, 与是否执行动作无关


def test_apply_only_on_executed():
    sim = {"4#": {"running": True, "flow": 250.0}, "5#": {"running": True, "flow": 240.0}}
    state = PolicyState()
    start = DispatchResult(status="success", as_of="2026-09-01T00:05:00", policy_version="t",
                           device_actions=[DeviceAction("4#", "stop", 0.0, 0.0, False),
                                           DeviceAction("5#", "hold", 240.0, 0.9, True)])
    # 不可执行 (含过渡缺口) -> 不推进
    bad = _cand(rc=[C.RC_TRANSITION_CAPACITY_GAP], actions=start.device_actions)
    assert B.executable(bad) is False
    # 模拟框架: 只有 executable 才调用 _apply
    if B.executable(start):
        B._apply(sim, start, "2026-09-01T00:05:00", state)
    assert sim["4#"]["running"] is False
    assert state.last_switch_at.get("4#") == "2026-09-01T00:05:00"
    # 未执行 bad -> 不改变 (重新构造干净 sim 验证)
    sim2 = {"4#": {"running": True, "flow": 250.0}}
    st2 = PolicyState()
    if B.executable(bad):
        B._apply(sim2, bad, "2026-09-01T00:05:00", st2)
    assert sim2["4#"]["running"] is True and not st2.last_switch_at


def test_switch_budget_counts_recent_switches():
    con = C.DispatchConstraints.from_config(
        {"switching": {"min_run_minutes": 15, "min_stop_minutes": 15, "max_switches_per_hour": 2}})
    state = PolicyState(recent_switch_times=["2026-09-01T00:00:00", "2026-09-01T00:10:00"])
    # 距 00:20 仍在一小时内 -> 预算用尽
    ok = CONS._switch_budget_ok(state, pd.Timestamp("2026-09-01T00:20:00"), con)
    assert ok is False
    ok2 = CONS._switch_budget_ok(state, pd.Timestamp("2026-09-01T01:15:00"), con)
    assert ok2 is True
