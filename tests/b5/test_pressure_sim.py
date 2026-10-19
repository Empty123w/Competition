"""压力仿真 v2 单元测试: 单位一致性 / 可复现随机 / 供气核算 / 闭环因果时序。

验证 P0 修正: dt 显式积分、CRN 固定种子、反馈指标用自身供气、控制器仅用已知压力。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
_s = importlib.util.spec_from_file_location("psim_v2", ROOT / "analysis" / "scripts" / "pressure_sim_dynamics_v2.py")
PS = importlib.util.module_from_spec(_s); _s.loader.exec_module(PS)


def test_dt_integration_scales_with_k_vol():
    """噪声/泄漏=0、开环时, 单步压力变化 = dt*surplus/K_VOL (单位一致, 线性)。"""
    supply = np.array([300.0]); demand = np.array([200.0])
    p1, _ = PS._simulate(supply, demand, 23700.0, 0.0, None, closed_loop=False)
    p2, _ = PS._simulate(supply, demand, 47400.0, 0.0, None, closed_loop=False)
    dp1, dp2 = p1[0] - PS.P0, p2[0] - PS.P0
    assert dp1 == np.float64(PS.DT * 100.0 / 23700.0) or abs(dp1 - PS.DT * 100.0 / 23700.0) < 1e-9
    assert abs(dp1 / dp2 - 2.0) < 1e-6   # K_VOL 翻倍 -> 变化减半


def test_noise_reproducible_fixed_seed():
    n = 50
    a = PS._noise(n, 0.003, PS.SEED_BASE + 1)
    b = PS._noise(n, 0.003, PS.SEED_BASE + 1)
    assert np.array_equal(a, b)
    assert a.std() > 0.0


def test_common_random_numbers_identical_paths_given_same_noise():
    supply = np.full(20, 250.0); demand = np.full(20, 240.0)
    noise = PS._noise(20, 0.003, 999)
    p_a, s_a = PS._simulate(supply, demand, PS.K_VOL, PS.LEAK_PER_MIN, noise)
    p_b, s_b = PS._simulate(supply, demand, PS.K_VOL, PS.LEAK_PER_MIN, noise)
    assert np.array_equal(p_a, p_b) and np.array_equal(s_a, s_b)


def test_supply_accounting_uses_effective_supply():
    demand = np.full(4, 200.0)
    sup = np.array([220.0, 220.0, 220.0, 220.0])
    p = np.full(4, 0.60)
    m = PS._metrics(p, sup, demand)
    assert m["supply_m3"] == round(float(sup.sum()) * PS.STEP, 1)
    assert m["shortfall_m3"] == 0.0


def test_shortfall_uses_given_supply_not_other_series():
    demand = np.array([300.0, 300.0])
    sup = np.array([200.0, 300.0])
    m = PS._metrics(np.full(2, 0.60), sup, demand)
    assert m["shortfall_m3"] == round(100.0 * PS.STEP, 1)   # 仅第一步缺 100


def test_controller_is_causal_uses_previous_pressure():
    # 开环 vs 闭环首步: 闭环首步供气由 P0 决定 (尚未使用未来压力)
    supply = np.array([250.0]); demand = np.array([240.0])
    p, se = PS._simulate(supply, demand, PS.K_VOL, 0.0, None, closed_loop=True)
    expect_sup = min(250.0 + PS.KC * (PS.P_SET - PS.P0), PS.SUP_CAP)
    assert abs(se[0] - expect_sup) < 1e-6
    assert abs(p[0] - (PS.P0 + PS.DT * ((expect_sup - 240.0) / PS.K_VOL))) < 1e-9
