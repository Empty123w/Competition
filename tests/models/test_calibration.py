"""A6 修复测试: 校准区间覆盖率、45/75 补发、需求口径列、原版保护。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HANDOFF = Path("artifacts/handoff_b")
CAL_VAL = HANDOFF / "forecast_validation_dispatch_calibrated.parquet"
CAL_DEV = HANDOFF / "forecast_dev_dispatch_calibrated.parquet"
SC_CAL = HANDOFF / "forecast_scoring_actuals_calibrated.parquet"
ORIG = HANDOFF / "forecast_validation_dispatch.parquet"
CORE = ["as_of", "target_time", "horizon_minutes", "predicted_flow", "lower_bound", "upper_bound"]
HORIZONS = {15, 30, 45, 60, 75}


def _need():
    if not CAL_VAL.exists():
        pytest.skip("[lightweight-no-data] 缺少 calibrated 产物")


def test_original_preserved():
    d = pd.read_parquet(ORIG)
    assert list(d.columns) == CORE           # 原版六列保持
    assert set(d["horizon_minutes"].unique()) <= {15, 30, 60}


def test_calibrated_schema_and_horizons():
    _need()
    d = pd.read_parquet(CAL_VAL)
    assert list(d.columns) == CORE + ["pred_mean_interval", "pred_upper_interval"]
    assert set(d["horizon_minutes"].unique()) == HORIZONS
    assert not d.duplicated(["as_of", "horizon_minutes"]).any()


def test_bounds_and_demand_columns():
    _need()
    d = pd.read_parquet(CAL_VAL)
    assert (d["lower_bound"] <= d["predicted_flow"]).all()
    assert (d["predicted_flow"] <= d["upper_bound"]).all()
    # 需求上界 = <=h 上界累计最大, 必 >= 本行 upper_bound
    assert (d["pred_upper_interval"] >= d["upper_bound"] - 1e-6).all()


def test_coverage_in_band_and_symmetric():
    _need()
    sc = pd.read_parquet(SC_CAL)
    sc = sc[sc["split"] == "valid"]
    for h in HORIZONS:
        s = sc[sc["horizon_minutes"] == h].dropna(subset=["actual_flow"])
        y = s["actual_flow"].to_numpy()
        cov = np.mean((y >= s["lower_bound"]) & (y <= s["upper_bound"]))
        low = np.mean(y < s["lower_bound"]); high = np.mean(y > s["upper_bound"])
        assert 0.85 <= cov <= 0.95, f"h={h} coverage={cov}"
        assert abs(low - high) < 0.03, f"h={h} 违反不对称 {low}/{high}"


def test_wape_small():
    _need()
    sc = pd.read_parquet(SC_CAL); sc = sc[sc["split"] == "valid"]
    for h in (15, 60):
        s = sc[sc["horizon_minutes"] == h].dropna(subset=["actual_flow"])
        y = s["actual_flow"].to_numpy(); p = s["predicted_flow"].to_numpy()
        wape = np.sum(np.abs(y - p)) / max(np.sum(np.abs(y)), 1.0) * 100
        assert wape <= 5.0, f"h={h} WAPE={wape}"
