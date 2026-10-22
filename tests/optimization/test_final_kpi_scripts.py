"""最终指标计算脚本的回归测试 (确保既有结果未被破坏)。

- 加载率 D3 公式 (final_load_ratio._stats)
- 能效 service 口径与 SEC 计算 (final_energy_verify._scope/_block)
- joint pooled SEC 改善 ≈ 7.731% (真实历史回测账本回归)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis/scripts"))

import final_load_ratio as FLR  # noqa: E402
import final_energy_verify as FEV  # noqa: E402


def _frame(f4, f5):
    d = {"obs": np.full(len(f4), 400.0)}
    for s in FLR.STRATS:
        d[f"{s}_f4"] = f4; d[f"{s}_f5"] = f5
        d[f"{s}_flow"] = f4 + f5
        d[f"{s}_status"] = "success"
        d[f"{s}_exec"] = True
        d[f"{s}_power"] = np.full(len(f4), 1000.0)
    return pd.DataFrame(d)


def test_d3_formula_both_and_single():
    # 三台情形: 双机等分(|Δλ|=0), 双机不等分(|Δλ|=0.2>0.1), 单机
    f4 = np.array([134.45, 200.0, 150.0])
    f5 = np.array([134.45, 100.0, 0.0])
    g = _frame(f4, f5)
    m = FLR._stats(g, "joint")
    assert m["n_both"] == 2 and m["n_single"] == 1
    # 第1步达标, 第2步不达标, 第3步(单机)达标 -> D3_all=2/3; D3_both=1/2
    assert abs(m["d3_all"] - 2 / 3) < 1e-3
    assert abs(m["d3_both"] - 0.5) < 1e-3
    # λ4=200/268.9, λ5=100/268.9 -> |Δλ| = 100/268.9 = 37.19pp
    assert abs(m["gap_max_pp"] - (100.0 / 268.9 * 100)) < 0.05


def test_scope_service_equiv():
    df = _frame(np.array([200.0, 200.0]), np.array([200.0, 200.0]))
    df["obs"] = [400.0, 500.0]          # 第2步供气400 < obs-0.05 -> 非 service
    for s in FEV.STRATS:
        if s != "joint":
            df[f"{s}_flow"] = 500.0
    svc = FEV._scope(df)
    assert svc.tolist() == [True, False]


def test_sec_and_saving_formula():
    g = _frame(np.array([200.0, 200.0]), np.array([200.0, 200.0]))
    for s in FEV.STRATS:
        g[f"{s}_power"] = 1000.0 if s != "joint" else 900.0
    b = FEV._block(g)
    # E,V 相同 -> SEC 降幅 10%
    assert abs(b["joint"]["sec_reduction_pct"] - 10.0) < 0.05
    assert abs(b["joint"]["saving_pct"] - 10.0) < 0.05


def test_pooled_joint_sec_regression():
    """真实历史账本回归: joint pooled service_equiv SEC 改善 ≈ 7.731%。"""
    FIN = ROOT / "analysis/reports/b/final"
    frames = [pd.read_csv(FIN / f"backtest_{w}_ledger.csv") for w in ("dev", "valid", "test")]
    pooled = pd.concat(frames, ignore_index=True)
    gp = pooled.loc[FEV._scope(pooled)]
    b = FEV._block(gp)
    assert abs(b["joint"]["sec_reduction_pct"] - 7.731) < 0.05
    assert b["joint"]["supply_diff_vs_b1_pct"] < 0     # joint 供气略低于 b1


def test_run_segmentation():
    import final_pressure_dev_analysis as FDA
    mask = np.array([False, True, True, False, True, True, True, False])
    assert FDA._runs(mask) == [(1, 2), (4, 6)]
