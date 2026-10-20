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


def test_demand_series_for_steps_holds_previous_no_future_leak(tmp_path):
    # A 只给 15/30/60; 规划器 step=15, n=4 需要 15/30/45/60 -> 45 缺, 回退上一可用(30)
    p = _write(tmp_path, [
        ["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, 105.0],
        ["2026-03-26 00:00:00", 30, 120.0, 100.0, 130.0, 125.0],
        ["2026-03-26 00:00:00", 60, 150.0, 130.0, 170.0, 160.0],
    ])
    b = load_forecast_bundle(p)
    series = b.demand_series_for_steps("2026-03-26 00:00:00", step_minutes=15, n_steps=4)
    assert series == [100.0, 120.0, 120.0, 150.0]   # 第3步(45min)回退30min预测; 无未来泄漏


def test_demand_series_for_steps_strict_raises(tmp_path):
    p = _write(tmp_path, [["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, None]])
    b = load_forecast_bundle(p)
    with pytest.raises(KeyError):
        b.demand_series_for_steps("2026-03-26 00:00:00", step_minutes=15, n_steps=2, missing_policy="strict")


def _write_a6(tmp_path, rows, name="a6.parquet"):
    df = pd.DataFrame(rows, columns=["as_of", "target_time", "horizon_minutes",
                                     "predicted_flow", "lower_bound", "upper_bound"])
    p = tmp_path / name
    df.to_parquet(p)
    return p


def test_a6_as_of_and_target_time(tmp_path):
    p = _write_a6(tmp_path, [
        ["2026-03-26 00:00:00", "2026-03-26 00:15:00", 15, 100.0, 90.0, 110.0],
        ["2026-03-26 00:00:00", "2026-03-26 00:30:00", 30, 120.0, 100.0, 130.0],
    ])
    b = load_forecast_bundle(p)
    fr = b.forecast_result("2026-03-26 00:00:00", 30)
    assert fr.predicted_flow == 120.0
    assert b.demand_series_for_steps("2026-03-26 00:00:00", 15, 2) == [100.0, 120.0]


def test_a6_target_time_mismatch_raises(tmp_path):
    p = _write_a6(tmp_path, [["2026-03-26 00:00:00", "2026-03-26 00:99:00", 15, 100.0, 90.0, 110.0]])
    with pytest.raises(ValueError):
        load_forecast_bundle(p)


def test_attach_scoring_actuals_not_used_as_input(tmp_path):
    f = _write_a6(tmp_path, [["2026-03-26 00:00:00", "2026-03-26 00:15:00", 15, 100.0, 90.0, 110.0]])
    scoring = pd.DataFrame({
        "split": ["dev"], "as_of": [pd.Timestamp("2026-03-26 00:00:00")],
        "target_time": [pd.Timestamp("2026-03-26 00:15:00")], "horizon_minutes": [15],
        "actual_flow": [105.0], "actual_available": [True]})
    b = load_forecast_bundle(f, scoring=scoring, scoring_split="dev")
    assert b.actual_at_target("2026-03-26 00:00:00", 15) == 105.0
    # actual 不进入调度输入
    assert b.forecast_result("2026-03-26 00:00:00", 15).predicted_flow == 100.0


def test_unavailable_actual_is_skipped(tmp_path):
    f = _write_a6(tmp_path, [["2026-03-26 00:00:00", "2026-03-26 00:15:00", 15, 100.0, 90.0, 110.0]])
    scoring = pd.DataFrame({
        "split": ["dev"], "as_of": [pd.Timestamp("2026-03-26 00:00:00")],
        "target_time": [pd.Timestamp("2026-03-26 00:15:00")], "horizon_minutes": [15],
        "actual_flow": [float("nan")], "actual_available": [False]})
    b = load_forecast_bundle(f, scoring=scoring)
    assert b.actual_at_target("2026-03-26 00:00:00", 15) is None


def test_load_real_a6_handoff_bundle():
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    disp = root / "artifacts" / "handoff_b" / "forecast_validation_dispatch.parquet"
    scor = root / "artifacts" / "handoff_b" / "forecast_scoring_actuals.parquet"
    if not disp.exists() or not scor.exists():
        pytest.skip("A6 交接文件缺失")
    b = load_forecast_bundle(disp, scoring=pd.read_parquet(scor), scoring_split="valid")
    assert b.horizons == [15, 30, 60]
    assert b.meta.sample_minutes == 5
    series = b.demand_series_for_steps("2026-08-01 00:00:00", step_minutes=15, n_steps=4)  # 15/30/45/60, 45 回退30
    assert len(series) == 4


def test_horizon_30_alignment(tmp_path):
    p = _write(tmp_path, [
        ["2026-03-26 00:00:00", 15, 100.0, 90.0, 110.0, None],
        ["2026-03-26 00:00:00", 30, 120.0, 100.0, 130.0, None],
    ])
    b = load_forecast_bundle(p)
    # step=30, n=2 -> 需要 30/60; 60 缺 -> 回退 30
    assert b.demand_series_for_steps("2026-03-26 00:00:00", 30, 2) == [120.0, 120.0]
