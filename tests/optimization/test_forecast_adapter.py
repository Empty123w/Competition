"""A 历史预测接入适配器测试 (合成数据; A 正式预测到达后复用同一适配器)。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.optimization.forecast_adapter import ForecastMeta, load_forecast_bundle


def _write(tmp_path, rows):
    df = pd.DataFrame(rows, columns=["time", "horizon_minutes", "predicted_flow",
                                     "lower_bound", "upper_bound", "actual_flow"])
    p = tmp_path / "forecast.parquet"
    df.to_parquet(p)
    return p


def test_mapping_and_target_time(tmp_path):
    p = _write(tmp_path, [
        ["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, 105.0],
        ["2026-03-26 00:00:00", 30, 120.0, 100.0, 130.0, 128.0],
        ["2026-03-26 00:15:00", 15, 130.0, 120.0, 140.0, 128.0],
    ])
    b = load_forecast_bundle(p)
    assert b.horizons == [15, 30]
    fr = b.forecast_result("2026-03-26 00:00:00", 30)
    assert fr.predicted_flow == 120.0 and fr.upper_bound == 130.0 and fr.horizon_minutes == 30
    # 目标时刻 = 发出 + horizon (真值仅依赖目标时刻)
    assert b.actual_at_target("2026-03-26 00:00:00", 30) == 128.0
    assert b.actual_at_target("2026-03-26 00:15:00", 15) == 128.0


def test_demand_series_aligns_targets(tmp_path):
    p = _write(tmp_path, [
        ["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, None],
        ["2026-03-26 00:00:00", 30, 120.0, 100.0, 130.0, None],
    ])
    b = load_forecast_bundle(p)
    assert b.demand_series("2026-03-26 00:00:00", [15, 30]) == {15: 100.0, 30: 120.0}


def test_missing_horizon_raises(tmp_path):
    p = _write(tmp_path, [["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, None]])
    b = load_forecast_bundle(p)
    with pytest.raises(KeyError):
        b.demand_series("2026-03-26 00:00:00", [15, 60])   # 缺 60 分钟


def test_sample_minutes_and_cutoff_inferred(tmp_path):
    p = _write(tmp_path, [
        ["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, None],
        ["2026-03-26 00:15:00", 15, 101.0, 91.0, 111.0, None],
    ])
    b = load_forecast_bundle(p)
    assert b.meta.sample_minutes == 15
    assert pd.Timestamp(b.meta.data_cutoff) == pd.Timestamp("2026-03-26 00:30:00")


def test_actual_never_in_forecast_result(tmp_path):
    p = _write(tmp_path, [["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, 999.0]])
    b = load_forecast_bundle(p)
    fr = b.forecast_result("2026-03-26 00:00:00", 15)
    # actual_flow 只进 actual_by_target, 不进入调度输入
    assert fr.predicted_flow == 100.0
    assert b.actual_at_target("2026-03-26 00:00:00", 15) == 999.0


def test_missing_column_raises(tmp_path):
    p = tmp_path / "bad.parquet"
    pd.DataFrame({"time": ["2026-03-26 00:00:00"], "predicted_flow": [1.0]}).to_parquet(p)
    with pytest.raises(ValueError):
        load_forecast_bundle(p)


def test_meta_quantile_defaults():
    m = ForecastMeta()
    assert m.quantile_lower == 0.05 and m.quantile_upper == 0.95
