"""能量感知均衡调度 (energy_aware_balanced) 单元测试。"""
from __future__ import annotations

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import energy_aware_balanced as EAB
from air_station.optimization import unit_commitment as UC
from air_station.optimization.service import load_optimization_config

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps(max_flow=268.9):
    return {d: CONS.DeviceCapability(d, True, False, 120.0, max_flow, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def _plan(cfg):
    con, caps = _con(), _caps()
    req = [420.0, 420.0, 420.0, 420.0]
    return EAB.plan(req, caps, con, {"4#", "5#"}, {"4#": 210.0, "5#": 210.0}, {},
                    "2026-08-01 00:00:00", model_path=MODEL, power_cap=1600.0, cfg=cfg)


def test_cap_inf_returns_balanced():
    b = _plan(EAB.EABConfig(lb_frac=0.5, cost_cap_kwh_per_step=float("inf")))
    assert b is not None
    f = b["alloc0"]["flows"]
    for d, q in f.items():
        assert q / UC.RATED >= 0.5 - 1e-6


def test_cap_zero_returns_min_energy():
    b = _plan(EAB.EABConfig(lb_frac=0.5, cost_cap_kwh_per_step=0.0))
    assert b is not None
    # 与纯最小能耗方案一致
    con, caps = _con(), _caps()
    ref = UC.plan([420.0] * 4, caps, con, {"4#", "5#"}, {"4#": 210.0, "5#": 210.0}, {},
                  "2026-08-01 00:00:00", model_path=MODEL, power_cap=1600.0,
                  cfg=UC.UCConfig(balanced=False, demand_basis="upper"), cache={})
    assert abs(b["energy_kwh"] - ref["energy_kwh"]) < 1e-6


def test_cap_limits_extra_energy():
    b_inf = _plan(EAB.EABConfig(lb_frac=0.5, cost_cap_kwh_per_step=float("inf")))
    b_cap = _plan(EAB.EABConfig(lb_frac=0.5, cost_cap_kwh_per_step=0.0001))
    assert b_cap["energy_kwh"] <= b_inf["energy_kwh"] + 1e-6


def test_lb_frac_raises_lower_bound():
    b = _plan(EAB.EABConfig(lb_frac=0.6, cost_cap_kwh_per_step=float("inf")))
    f = b["alloc0"]["flows"]
    for d, q in f.items():
        assert q / UC.RATED >= 0.6 - 1e-6


def test_day_one_no_regression_on_uc_default():
    # 默认 UCConfig 行为不变 (lb_frac 默认 0.5)
    con, caps = _con(), _caps()
    a = UC.best_allocation(("4#", "5#"), 420.0, caps, {}, {}, MODEL, UC.UCConfig(balanced=True), {})
    b = UC.best_allocation(("4#", "5#"), 420.0, caps, {}, {}, MODEL,
                           UC.UCConfig(balanced=True, lb_frac=0.5), {})
    assert abs(a["power_kw"] - b["power_kw"]) < 1e-6
