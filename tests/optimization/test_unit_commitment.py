"""新机组组合优化器 (unit_commitment) 单元测试。"""
from __future__ import annotations

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import unit_commitment as UC
from air_station.optimization.service import load_optimization_config

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps(max_flow=268.9):
    return {d: CONS.DeviceCapability(d, True, False, 120.0, max_flow, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def test_allocation_single_meets_required():
    cfg = UC.UCConfig()
    al = UC.best_allocation(("4#",), 200.0, _caps(), {}, {}, MODEL, cfg, {})
    assert al["shortfall"] == 0.0
    assert abs(al["flows"]["4#"] - 200.0) < 1.0


def test_allocation_double_meets_required_and_bounds():
    cfg = UC.UCConfig()
    al = UC.best_allocation(("4#", "5#"), 350.0, _caps(), {}, {}, MODEL, cfg, {})
    assert al["shortfall"] == 0.0
    s = sum(al["flows"].values())
    assert s + 1e-6 >= 350.0
    for d, f in al["flows"].items():
        assert 120.0 - 1e-6 <= f <= 268.9 + 1e-6


def test_allocation_shortfall_when_capacity_insufficient():
    cfg = UC.UCConfig()
    al = UC.best_allocation(("4#",), 400.0, _caps(), {}, {}, MODEL, cfg, {})
    assert al["shortfall"] > 0.0   # 单机上限 268.9


def test_ramp_limits_change_when_prev_high():
    cfg = UC.UCConfig()
    # 4# 前一分钟在 260, 本步坡上限 77 -> 不允许骤降到 120
    al = UC.best_allocation(("4#",), 150.0, _caps(), {"4#": 260.0}, {"4#": True}, MODEL, cfg, {})
    assert al["flows"]["4#"] >= 260.0 - UC.RAMP_STEP["4#"] - 1e-6


def test_plan_feasible_and_reserve():
    con, caps = _con(), _caps()
    req = [300.0, 300.0, 300.0, 300.0]
    b = UC.plan(req, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {}, "2026-08-01 00:00:00",
                model_path=MODEL, power_cap=1600.0, cfg=UC.UCConfig(demand_basis="upper"))
    assert b is not None
    for k, combo in enumerate(b["seq"]):
        a = CONS.assess_combination(combo, caps, req[k], con.reserve)
        assert a.feasible


def test_plan_none_when_capacity_infeasible():
    con, caps = _con(), _caps()
    assert UC.plan([999.0] * 4, caps, con, set(), {}, {}, "2026-08-01 00:00:00",
                   model_path=MODEL, cfg=UC.UCConfig()) is None


def test_demand_for_bases():
    cfg = UC.UCConfig(demand_basis="mid")
    assert UC.demand_for(100.0, 140.0, cfg) == 120.0
    cfg2 = UC.UCConfig(demand_basis="pred_plus", margin_m3min=20.0)
    assert UC.demand_for(100.0, 140.0, cfg2) == 120.0
    assert UC.demand_for(100.0, 140.0, UC.UCConfig(demand_basis="upper")) == 140.0
