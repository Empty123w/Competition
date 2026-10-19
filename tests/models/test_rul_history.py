"""阶段 A6 历史 RUL 迁移估计测试。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

R = Path("artifacts/metrics/rul_history.parquet")
COLS = {"time", "device_id", "source_time", "input_data_cutoff_time", "estimated_rul_hours",
        "lower_bound_hours", "upper_bound_hours", "method", "validation_mode", "usage_context",
        "input_origin", "model_training_origin", "model_version", "median_model_sha256",
        "lower_model_sha256", "upper_model_sha256", "quality_valid", "quality_reason",
        "out_of_distribution", "ood_feature_count", "prediction_clipped", "stale_hours",
        "fresh_prediction", "window_sample_count", "window_coverage_rate", "valid_until",
        "max_staleness_hours"}


def _need():
    if not R.exists():
        pytest.skip("[lightweight-no-data] 缺少 rul_history.parquet")


def test_columns_and_metadata():
    _need()
    d = pd.read_parquet(R)
    assert set(d.columns) == COLS
    assert (d["validation_mode"] == "simulation").all()
    assert (d["usage_context"] == "real_telemetry_transfer").all()
    assert (d["input_origin"] == "real_telemetry").all()
    assert (d["model_training_origin"] == "simulated").all()
    assert set(d["device_id"].unique()) == {4, 5}


def test_bounds_nonneg_and_finite():
    _need()
    d = pd.read_parquet(R)
    assert (d["lower_bound_hours"] <= d["estimated_rul_hours"]).all()
    assert (d["estimated_rul_hours"] <= d["upper_bound_hours"]).all()
    assert (d["estimated_rul_hours"] >= 0).all() and (d["lower_bound_hours"] >= 0).all()
    assert np.isfinite(d["estimated_rul_hours"]).all()


def test_time_semantics_and_monotonic():
    _need()
    d = pd.read_parquet(R)
    assert (pd.to_datetime(d["source_time"]) <= pd.to_datetime(d["time"])).all()
    assert (pd.to_datetime(d["input_data_cutoff_time"]) <= pd.to_datetime(d["time"])).all()
    assert ((pd.to_datetime(d["valid_until"]) - pd.to_datetime(d["time"])).dt.total_seconds() == 6 * 3600).all()
    for _, s in d.groupby("device_id"):
        assert s.sort_values("time")["time"].is_monotonic_increasing


def test_first_candidate_not_before_warmup():
    _need()
    d = pd.read_parquet(R)
    assert pd.to_datetime(d["time"]).min() >= pd.Timestamp("2026-03-14 00:00:00")


def test_ood_flag_consistency():
    _need()
    d = pd.read_parquet(R)
    ood = d[d["out_of_distribution"]]
    assert (~ood["quality_valid"]).all()
    assert (ood["ood_feature_count"] > 0).all()
