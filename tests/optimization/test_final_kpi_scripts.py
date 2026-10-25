"""最终指标计算脚本的回归测试 (确保既有结果未被破坏)。

- 加载率 D3 公式 (final_load_ratio._stats)
- 能效 service 口径与 SEC 计算 (final_energy_verify._scope/_block)
- joint pooled SEC 改善 ≈ 7.731% (真实历史回测账本回归)
"""
from __future__ import annotations

import json
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


def test_load_ratio_deviation_and_NA():
    # 双机等分(|D|=0pp), 双机不等分(|D|=37.19pp), 单机(NA)
    f4 = np.array([134.45, 200.0, 150.0])
    f5 = np.array([134.45, 100.0, 0.0])
    g = _frame(f4, f5)
    m = FLR._stats(g, "joint")
    assert m["n_both"] == 2 and m["n_single"] == 1 and m["n_none"] == 0
    # 主指标: 偏离度 (pp), 仅双机样本; 单机 NA 不计入
    assert abs(m["deviation_max_pp"] - (100.0 / 268.9 * 100)) < 0.05
    assert m["deviation_mean_pp"] is not None
    assert m["all_within_limit"] is False        # 37.19pp > 10pp
    assert abs(m["pass_rate_both"] - 0.5) < 1e-3  # 双机 1/2 达标
    # 覆盖率: 双机样本占比
    assert abs(m["both_coverage"] - 2 / 3) < 1e-3


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


def test_pooled_joint_load_ratio_regression():
    """真实历史账本回归: joint pooled 偏离度 平均 8.046pp / P95 9.862pp / 最大 9.999pp。"""
    import final_load_ratio as FLR  # noqa
    FIN = ROOT / "analysis/reports/b/final"
    frames = [pd.read_csv(FIN / f"backtest_{w}_ledger.csv") for w in ("dev", "valid", "test")]
    pooled = pd.concat(frames, ignore_index=True)
    gp = pooled.loc[FLR._scope(pooled)]
    m = FLR._stats(gp, "joint")
    assert abs(m["deviation_mean_pp"] - 8.046) < 0.02
    assert abs(m["deviation_p95_pp"] - 9.862) < 0.05
    assert abs(m["deviation_max_pp"] - 9.999) < 0.02
    assert m["all_within_limit"] is True
    assert m["pass_rate_both"] >= 0.99
    assert m["n_both"] > 0 and m["both_coverage"] < 1.0   # 存在单机样本, 覆盖率<1


def test_joint_nompc_window_sec_regression():
    """真实历史账本回归: joint_nompc 分窗口 SEC 改善 (统一六策略 service 口径)。"""
    import final_test_energy_diagnosis as TED
    FIN = ROOT / "analysis/reports/b/final"
    exp = {"dev": 10.49, "valid": 3.844, "test": 2.978}
    for w, e in exp.items():
        df = pd.read_csv(FIN / f"backtest_{w}_ledger.csv")
        g = df.loc[TED._scope(df, TED.STRATS)]
        b = TED._block(g)
        assert abs(b["joint_nompc"]["sec_reduction_pct"] - e) < 0.05, (w, b["joint_nompc"]["sec_reduction_pct"])
        assert b["joint_nompc"]["low_reduction_pct"] is not None
    # 统一口径样本数 (纳入 joint_nompc 不缩小样本)
    df = pd.read_csv(FIN / "backtest_test_ledger.csv")
    assert int(TED._scope(df, TED.STRATS).sum()) == int(TED._scope(df, TED.S6_OLD).sum())


def test_loadratio_robustness_regression():
    """稳健性对照: 9pp 版最大偏离=9.0pp; 10pp 版<=10pp; 9pp 版仍满足能效/低负荷。"""
    import final_loadratio_robustness as FLB
    rep = json.loads((ROOT / "analysis/reports/b/final/loadratio_robustness.json").read_text(encoding="utf-8"))
    v10 = rep["pooled"]["v10"]; v09 = rep["pooled"]["v09"]
    assert abs(v09["deviation_max_pp"] - 9.0) < 0.02
    assert v10["deviation_max_pp"] <= 10.0 + 1e-6
    assert v09["sec_reduction_pct"] >= 6.0
    assert v09["low_reduction_pct"] >= 60.0
    assert v09["infeasible_steps"] == 0 and v09["blocked_steps"] == 0
