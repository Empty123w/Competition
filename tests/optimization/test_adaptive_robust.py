"""Adaptive Robust Dispatch 单元测试。"""
from __future__ import annotations

import pandas as pd

from air_station.baseline import rules as B1
from air_station.optimization import adaptive_robust as ARD
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization.service import load_optimization_config

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps():
    return {d: CONS.DeviceCapability(d, True, False, 120.0, 268.9, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def test_estimator_is_causal():
    cfg = ARD.ARDConfig()
    e = ARD.ResidualEstimator(cfg)
    e.update("2026-07-01 00:00:00", 200.0, 190.0)     # 已实现
    e.update("2026-07-02 00:00:00", 300.0, 100.0)     # 未来 (相对 as_of=07-01 12:00 未实现)
    r = e._realized("2026-07-01 12:00:00")
    assert len(r) == 1 and abs(r[0] - 10.0) < 1e-9


def test_margin_capped_between_pred_and_upper():
    cfg = ARD.ARDConfig(method="ewma", min_history=1)
    e = ARD.ResidualEstimator(cfg)
    for i in range(10):
        e.update(pd.Timestamp("2026-07-01 00:00:00") + pd.Timedelta(minutes=i), 260.0, 200.0)  # 残差 +60
    m = e.margin("2026-07-01 02:00:00", pred=200.0, ub=230.0, cfg=cfg)
    assert 0.0 <= m <= 30.0     # cap_to_upper


def test_margin_none_and_insufficient_history():
    e = ARD.ResidualEstimator(ARD.ARDConfig(method="none"))
    assert e.margin("2026-07-01 00:00:00", 200.0, 230.0, e.cfg) == 0.0
    e2 = ARD.ResidualEstimator(ARD.ARDConfig(method="ewma", min_history=50))
    e2.update("2026-07-01 00:00:00", 260.0, 200.0)
    # 历史不足 -> 不激进 (不超过 ub-pred)
    m = e2.margin("2026-07-01 01:00:00", 200.0, 230.0, e2.cfg)
    assert 0.0 <= m <= 30.0


def test_demand_sequence_within_bounds():
    cfg = ARD.ARDConfig(method="ewma", min_history=1)
    e = ARD.ResidualEstimator(cfg)
    for i in range(5):
        e.update(pd.Timestamp("2026-07-01 00:00:00") + pd.Timedelta(minutes=i), 250.0, 200.0)
    preds = [200.0, 210.0, 210.0, 205.0]; ubs = [230.0, 240.0, 240.0, 235.0]
    d, m = ARD.demand_sequence(preds, ubs, e, "2026-07-01 01:00:00", cfg)
    assert all(p - 1e-9 <= dd <= u + 1e-9 for dd, p, u in zip(d, preds, ubs))


def test_plan_ard_feasible():
    con, caps = _con(), _caps()
    cfg = ARD.ARDConfig(method="ewma", min_history=1)
    e = ARD.ResidualEstimator(cfg)
    for i in range(5):
        e.update(pd.Timestamp("2026-07-01 00:00:00") + pd.Timedelta(minutes=i), 250.0, 210.0)
    b = ARD.plan_ard([210.0] * 4, [230.0] * 4, caps, con, {"4#", "5#"}, {"4#": 210.0, "5#": 210.0},
                     {}, "2026-07-01 01:00:00", e, cfg, model_path=MODEL, power_cap=1600.0)
    assert b is not None
    assert len(b["demand_seq"]) == 4


def test_plan_ard_infeasible_returns_none():
    con, caps = _con(), _caps()
    cfg = ARD.ARDConfig()
    e = ARD.ResidualEstimator(cfg)
    assert ARD.plan_ard([999.0] * 4, [999.0] * 4, caps, con, set(), {}, {},
                        "2026-07-01 01:00:00", e, cfg, model_path=MODEL) is None
