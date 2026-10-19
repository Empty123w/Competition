"""阶段 A6 历史健康评分测试。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

H = Path("artifacts/metrics/health_history.parquet")
COLS = {"time", "device_id", "source_time", "input_data_cutoff_time", "health_score",
        "risk_level", "anomaly_score", "top_anomaly_factor", "trend_direction",
        "quality_valid", "quality_reason", "baseline_cutoff_time", "valid_until",
        "max_staleness_minutes", "validation_mode", "config_sha256"}
RISK = {"正常", "关注", "预警", "严重"}


def _need():
    if not H.exists():
        pytest.skip("[lightweight-no-data] 缺少 health_history.parquet")


def test_columns_and_metadata():
    _need()
    d = pd.read_parquet(H)
    assert set(d.columns) == COLS
    assert (d["validation_mode"] == "unsupervised_rolling").all()
    assert set(d["device_id"].unique()) == {4, 5}


def test_time_semantics():
    _need()
    d = pd.read_parquet(H)
    assert (pd.to_datetime(d["source_time"]) <= pd.to_datetime(d["time"])).all()
    assert (pd.to_datetime(d["input_data_cutoff_time"]) <= pd.to_datetime(d["time"])).all()
    assert ((pd.to_datetime(d["valid_until"]) - pd.to_datetime(d["time"])).dt.total_seconds() == 3600).all()


def test_baseline_cutoff_strictly_before():
    _need()
    d = pd.read_parquet(H).dropna(subset=["baseline_cutoff_time"])
    assert (pd.to_datetime(d["baseline_cutoff_time"]) < pd.to_datetime(d["time"])).all()


def test_not_running_has_no_score():
    _need()
    d = pd.read_parquet(H)
    nr = d[d["quality_reason"] == "not_running"]
    assert nr["health_score"].isna().all()
    assert (~nr["quality_valid"]).all()


def test_valid_scores_range_and_enum():
    _need()
    d = pd.read_parquet(H)
    v = d[d["quality_valid"]]
    assert ((v["health_score"] >= 0) & (v["health_score"] <= 100)).all()
    assert np.isfinite(v["health_score"]).all()
    assert set(v["risk_level"].dropna().unique()) <= RISK
