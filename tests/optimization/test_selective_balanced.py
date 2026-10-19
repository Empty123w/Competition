"""Selective Balanced Dispatch 单元测试。"""
from __future__ import annotations

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import selective_balanced as SBD
from air_station.optimization.service import load_optimization_config

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps():
    return {d: CONS.DeviceCapability(d, True, False, 120.0, 268.9, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def test_sbd_infeasible_both_none():
    con, caps = _con(), _caps()
    assert SBD.plan_sbd([999.0] * 4, caps, con, set(), {}, {}, "2026-08-01 00:00:00",
                        model_path=MODEL) is None


def test_sbd_threshold_zero_prefers_b3():
    con, caps = _con(), _caps()
    req = [300.0, 300.0, 300.0, 300.0]   # 双机, 需求>=268.9 -> bal 可减少低负荷
    b = SBD.plan_sbd(req, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {},
                     "2026-08-01 00:00:00", SBD.SBDConfig(max_extra_kwh_per_low_step=0.0),
                     model_path=MODEL, power_cap=1600.0)
    assert b is not None and b["chosen"] in ("b3", "b3_fallback")
    assert b["low_steps_bal"] <= b["low_steps_b3"]


def test_sbd_high_threshold_prefers_bal_when_it_reduces_low():
    con, caps = _con(), _caps()
    req = [300.0] * 4
    b = SBD.plan_sbd(req, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {},
                     "2026-08-01 00:00:00", SBD.SBDConfig(max_extra_kwh_per_low_step=1e9),
                     model_path=MODEL, power_cap=1600.0)
    assert b is not None
    if b["avoided_low_steps"] > 0:
        assert b["chosen"] == "bal"
        # 均衡分配: 双机流量均 >= 134.45
        flows = b["alloc0"]["flows"]
        for f in flows.values():
            assert f >= 134.45 - 1e-6


def test_sbd_alloc0_present():
    con, caps = _con(), _caps()
    req = [200.0, 200.0, 200.0, 200.0]
    b = SBD.plan_sbd(req, caps, con, {"4#", "5#"}, {"4#": 150.0, "5#": 150.0}, {},
                     "2026-08-01 00:00:00", model_path=MODEL, power_cap=1600.0)
    assert b is not None and "alloc0" in b and "flows" in b["alloc0"]
