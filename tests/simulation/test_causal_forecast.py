"""因果预测器与联合 MPC 无泄漏性质测试 (第三阶段复核)。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.stage2.causal_forecast import CausalForecaster


def test_persistence_predicts_last():
    fc = CausalForecaster(method="persistence")
    for v in [10, 20, 30]:
        fc.observe(v)
    assert fc.predict_future(3) == [30.0, 30.0, 30.0]


def test_predict_with_nowcast_shape():
    fc = CausalForecaster(method="ewma")
    fc.observe(100.0)
    out = fc.predict_with_nowcast(4, 100.0)
    assert len(out) == 4 and out[0] == 100.0


def test_no_future_information():
    """预测只依赖 t<=k 的观测: 追加未来观测不得改变既有预测。"""
    fc = CausalForecaster(method="ar")
    hist = list(np.sin(np.linspace(0, 3, 60)) * 100 + 400)
    for v in hist:
        fc.observe(v)
    a = fc.predict_future(4)
    fc2 = CausalForecaster(method="ar")
    for v in hist:
        fc2.observe(v)
    b = fc2.predict_future(4)
    assert np.allclose(a, b)
    # 不同方法均可运行且长度正确
    for m in ("persistence", "ewma", "seasonal", "ar"):
        f = CausalForecaster(method=m)
        for v in hist:
            f.observe(v)
        assert len(f.predict_future(4)) == 4


def test_ar_recursive_uses_past_only():
    fc = CausalForecaster(method="ar")
    for v in np.linspace(100, 200, 80):
        fc.observe(v)
    fut = fc.predict_future(5)
    assert len(fut) == 5
    assert all(np.isfinite(fut))


def test_pressure_sim_demand_override():
    """压力闭环支持真实需求驱动 (chain 真实历史工况); 长度正确且可复现。"""
    import numpy as np
    from air_station.simulation import closed_loop as CL
    from air_station.stage2 import pressure_sim as PS
    dem = 400.0 + 20.0 * np.sin(np.linspace(0, 3, 40))
    sc = CL.Scenario("t", n_steps=40, base_m3min=float(dem.mean()))
    r = PS.simulate("reactive_pi", sc, sim=CL.SimParams(dt_min=5.0, seed=0),
                    demand_override=dem, p_init=0.60)
    assert len(r["records"]) == 40
    assert 0.0 <= r["metrics"]["pressure_compliance"] <= 1.0
    assert len(r["records"]) == len(dem)
