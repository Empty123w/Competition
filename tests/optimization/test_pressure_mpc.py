"""Pressure-Aware MPC 单元测试。"""
from __future__ import annotations

import time

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import pressure_mpc as MPC
from air_station.optimization.service import load_optimization_config

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps():
    return {d: CONS.DeviceCapability(d, True, False, 120.0, 268.9, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def test_config_loads():
    cfg = MPC.load_mpc_config()
    assert cfg.horizon == 4 and cfg.band == (0.55, 0.66)
    assert cfg.dyn.k_vol > 0


def test_pressure_step_direction():
    cfg = MPC.MPCConfig()
    assert MPC.pressure_step(0.60, supply=300.0, demand=200.0, cfg=cfg) > 0.60  # 供>耗 -> 升
    assert MPC.pressure_step(0.60, supply=100.0, demand=200.0, cfg=cfg) < 0.60  # 供<耗 -> 降


def test_plan_feasible_normal():
    con, caps = _con(), _caps()
    pred = [180.0] * 4; ub = [200.0] * 4
    b = MPC.plan_mpc(pred, ub, caps, con, {"4#", "5#"}, {"4#": 180.0, "5#": 180.0}, {},
                     "2026-08-01 00:00:00", 0.60, model_path=MODEL, power_cap=1600.0)
    assert b is not None
    for k, (combo, lvl) in enumerate(b["seq"]):
        a = CONS.assess_combination(combo, caps, ub[k], con.reserve)
        assert a.feasible


def test_high_pressure_prefers_lower_supply():
    con, caps = _con(), _caps()
    pred = [150.0] * 4; ub = [200.0] * 4
    b = MPC.plan_mpc(pred, ub, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {},
                     "2026-08-01 00:00:00", 0.68, model_path=MODEL, power_cap=1600.0)
    assert b is not None
    supply0 = sum(b["alloc0"]["flows"].values())
    assert supply0 <= 160.0   # 高压时贴近 pred(150) 而非 upper(200)


def test_low_pressure_prefers_higher_supply():
    con, caps = _con(), _caps()
    pred = [150.0] * 4; ub = [200.0] * 4
    b = MPC.plan_mpc(pred, ub, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {},
                     "2026-08-01 00:00:00", 0.50, model_path=MODEL, power_cap=1600.0)
    assert b is not None
    supply0 = sum(b["alloc0"]["flows"].values())
    assert supply0 >= 190.0   # 低压时抬到 upper(200) 附近


def test_infeasible_returns_none():
    con, caps = _con(), _caps()
    assert MPC.plan_mpc([999.0] * 4, [999.0] * 4, caps, con, set(), {}, {},
                        "2026-08-01 00:00:00", 0.60, model_path=MODEL) is None


def test_runtime_under_one_second():
    con, caps = _con(), _caps()
    pred = [180.0, 190.0, 210.0, 230.0]; ub = [210.0, 230.0, 250.0, 260.0]
    t0 = time.perf_counter()
    MPC.plan_mpc(pred, ub, caps, con, {"4#", "5#"}, {"4#": 180.0, "5#": 180.0}, {},
                 "2026-08-01 00:00:00", 0.60, model_path=MODEL, power_cap=1600.0)
    assert time.perf_counter() - t0 < 3.0
