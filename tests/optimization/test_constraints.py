"""B3 硬约束与组合可行性测试。"""
from __future__ import annotations

import pandas as pd

from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization.contracts import PolicyState, ReservePolicy
from tests.optimization import _b3_helpers as H


def test_enumerate_four_combinations():
    con = H.constraints()
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    combos = [a.combo for a in CONS.enumerate_combinations(300.0, caps, con.reserve)]
    assert combos == [(), ("4#",), ("5#",), ("4#", "5#")]


def test_single_machine_feasible_when_enough():
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    a = CONS.assess_combination(("4#",), caps, 200.0, con.reserve)
    assert a.feasible and a.n1_capacity_m3min is None


def test_capacity_insufficient():
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    a = CONS.assess_combination(("4#", "5#"), caps, 560.0, con.reserve)
    assert not a.feasible and C.RC_INFEASIBLE_CAPACITY in a.reason_codes


def test_reserve_extra_blocks_single():
    con = H.constraints(reserve={"mode": "extra", "value": 0.05, "unit": "percent"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    a = CONS.assess_combination(("4#",), caps, 300.0, con.reserve)   # need=315 > 268.9
    assert not a.feasible
    assert CONS.assess_combination(("4#", "5#"), caps, 300.0, con.reserve).feasible


def test_n_minus_one_rule():
    con = H.constraints(reserve={"mode": "n_minus_one"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    # 双机时 N-1 后剩 268.9 < 300 -> 不可行
    assert not CONS.assess_combination(("4#", "5#"), caps, 300.0, con.reserve).feasible
    # 需求 200 时 N-1 可行
    assert CONS.assess_combination(("4#", "5#"), caps, 200.0, con.reserve).feasible


def test_transition_start_before_stop():
    con = H.constraints(reserve={"mode": "none"})
    # 当前仅 5# 运行, 目标切到 4#: 4# 可启动, 5# 可停机
    caps = CONS.build_capabilities(con, H.snapshot(running=(False, True)), [], None)
    ok, steps, rc = CONS.transition_feasibility(("5#",), ("4#",), caps, 200.0, con.reserve)
    assert ok and not rc
    assert [s["step"] for s in steps] == ["start", "stop"]        # 先启动替代, 后停机
    assert all(s["supply_ok"] for s in steps)


def test_transition_capacity_gap():
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    # 双机->仅4#, 但需求 300 > 单机容量 268.9 -> 停机步骤供气不足
    ok, steps, rc = CONS.transition_feasibility(("4#", "5#"), ("4#",), caps, 300.0, con.reserve)
    assert not ok and C.RC_TRANSITION_CAPACITY_GAP in rc


def test_min_run_lock_blocks_transition():
    con = H.constraints(reserve={"mode": "none"})
    state = PolicyState(last_switch_at={"5#": "2026-09-10T23:55:00"})   # 5# 刚启动
    caps = CONS.build_capabilities(con, H.snapshot(), [], state)
    ok, steps, rc = CONS.transition_feasibility(("5#",), (), caps, 0.0, con.reserve)
    assert C.RC_MIN_RUN_LOCK in rc


def test_device_unavailable_under_derate_critical():
    con = H.constraints(health={"mode": "derate", "status": "simulation",
                                "derate_map": {"normal": 1.0, "attention": 1.0,
                                               "warning": 0.85, "critical": 0.0}})
    caps = CONS.build_capabilities(con, H.snapshot(), [H.health("4#", "严重")], None)
    assert caps["4#"].available is False and caps["4#"].can_start is False
    assert caps["5#"].available is True


def test_only_devices_4_and_5():
    assert set(C.DEVICE_IDS) == {"4#", "5#"}
