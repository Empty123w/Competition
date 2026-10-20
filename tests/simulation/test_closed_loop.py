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
