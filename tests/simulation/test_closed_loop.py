"""闭环仿真模块单元测试 (物理/质量平衡/放空/确定性/场景)。"""
from __future__ import annotations

import numpy as np
from dataclasses import replace

from air_station.simulation import closed_loop as CL


def test_pressure_rises_on_surplus():
    p = CL.SimParams()
    p0 = 0.60
    p1 = CL.pressure_step(p0, q_sup=400.0, q_dem=300.0, q_blow=0.0, params=p)
    assert p1 > p0


def test_pressure_falls_on_deficit():
    p = CL.SimParams()
    p1 = CL.pressure_step(0.60, q_sup=200.0, q_dem=400.0, q_blow=0.0, params=p)
    assert p1 < 0.60


def test_pressure_clamped_hard():
    p = CL.SimParams()
    p1 = CL.pressure_step(0.74, q_sup=1000.0, q_dem=0.0, q_blow=0.0, params=p)
    assert p1 <= p.p_hard_hi + 1e-9


def test_blowoff_only_above_band():
    p = CL.SimParams()
    assert CL.blowoff_flow(0.60, 400.0, p) == 0.0
    assert CL.blowoff_flow(0.70, 400.0, p) > 0.0


def test_blowoff_bounded_by_supply():
    p = CL.SimParams()
    assert CL.blowoff_flow(0.75, q_sup=50.0, params=p) <= 50.0


def test_demand_series_shape_and_range():
    sc = CL.SCENARIOS["normal"]
    d = CL.demand_series(sc)
    assert len(d) == sc.n_steps
    assert d.min() >= 0.0 and d.max() <= 2 * CL.RATED


def test_step_change_increases_demand():
    sc = CL.SCENARIOS["step"]
    d = CL.demand_series(sc)
    i0 = sc.step_change[0]
    assert d[i0:].mean() > d[:i0].mean()


def test_determinism_b3_low():
    sc = replace(CL.SCENARIOS["low"], n_steps=24)
    a = CL.simulate("b3", sc, CL.SimParams(seed=7))
    b = CL.simulate("b3", sc, CL.SimParams(seed=7))
    assert a["metrics"]["energy_kwh"] == b["metrics"]["energy_kwh"]
    assert a["metrics"]["pressure_compliance"] == b["metrics"]["pressure_compliance"]


def test_metrics_keys_present():
    sc = replace(CL.SCENARIOS["normal"], n_steps=24)
    r = CL.simulate("b1", sc, CL.SimParams(seed=0))
    for k in ("pressure_compliance", "pressure_std", "energy_kwh", "supply_m3",
              "supply_gap_m3", "low_load_min", "fallback_steps", "blowoff_m3"):
        assert k in r["metrics"]


def test_accounted_vent_at_high_clamp():
    p = CL.SimParams()
    pn, vent, unmet, flag = CL.pressure_step_accounted(0.75, 2000.0, 0.0, 0.0, p)
    assert flag == "high" and vent > 0.0 and unmet == 0.0
    assert abs(pn - p.p_hard_hi) < 1e-9


def test_accounted_unmet_at_low_clamp():
    p = CL.SimParams()
    pn, vent, unmet, flag = CL.pressure_step_accounted(0.40, 0.0, 2000.0, 0.0, p)
    assert flag == "low" and unmet > 0.0 and vent == 0.0
    assert abs(pn - p.p_hard_lo) < 1e-9


def test_single_step_mass_balance_identity():
    p = CL.SimParams()
    p0, qs, qd, qb = 0.62, 500.0, 300.0, 0.0
    pn, vent, unmet, _ = CL.pressure_step_accounted(p0, qs, qd, qb, p)
    net_in = (qs - qd - qb) * p.dt_min
    leak = p.k_vol_m3_per_mpa * p.leak_per_min * (p0 - p.p_env_mpa) * p.dt_min
    store = p.k_vol_m3_per_mpa * (pn - p0)
    assert abs(net_in - leak - store - vent + unmet) < 1e-6


def test_clamp_bounds_outside_band():
    p = CL.SimParams()
    CL.clamp_within_band_check(p)   # 不抛异常即通过


def test_run_mass_balance_closed_normal():
    r = CL.simulate("b3", CL.SCENARIOS["normal"], CL.SimParams(seed=0))
    assert abs(r["metrics"]["mass_balance_residual_m3"]) < 1.0


def test_run_mass_balance_closed_peak():
    r = CL.simulate("b3", CL.SCENARIOS["peak"], CL.SimParams(seed=0))
    assert abs(r["metrics"]["mass_balance_residual_m3"]) < 1.0
    assert r["metrics"]["clamp_low_steps"] > 0
    assert r["metrics"]["unserved_deficit_m3"] > 0


def test_all_strategies_same_initial_pressure():
    outs = {m: CL.simulate(m, CL.SCENARIOS["normal"], CL.SimParams(seed=0))["metrics"]["pressure_initial"]
            for m in ("b1", "b3", "balanced", "load_balanced")}
    assert len(set(outs.values())) == 1
