"""阶段 A3 特征工程测试: 防泄漏、幂等、无量纲异常、工况分层。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from air_station.models.forecasting import features as FF
from air_station.models.health import features as HF


# ----------------------------- forecast ----------------------------- #
def _station(n=40):
    t = pd.date_range("2026-04-01 00:00:00", periods=n, freq="1min")
    y = np.arange(n, dtype=float)
    return pd.DataFrame({
        "time": t,
        "header_pressure_mpa": np.full(n, 0.6),
        "total_flow_m3_min": y,
        "total_power_kw": y * 2.0,
        "station_temperature_c": np.full(n, 20.0),
        "station_humidity_percent": np.full(n, 50.0),
        "load_rate": y / 100.0,
        "load_level": np.full(n, 1),
    })


CFG = {"target": {"column": "total_flow_m3_min"},
       "features": {"lags_minutes": [1, 5, 15, 30, 60, 1440],
                    "rolling_minutes": [5, 15, 30, 60],
                    "diff_minutes": [1, 5, 15]}}


def test_no_future_leakage_rolling():
    df = FF.build_features(_station(), CFG)
    i = 20
    expected = np.mean(np.arange(i - 5, i))       # 过去5点(不含 i)
    assert df.loc[i, "roll_5_mean"] == pytest.approx(expected)


def test_no_future_leakage_lag():
    df = FF.build_features(_station(), CFG)
    assert df.loc[20, "lag_1"] == 19
    assert df.loc[20, "lag_5"] == 15


def test_forecast_idempotent():
    a = FF.build_features(_station(), CFG)
    b = FF.build_features(_station(), CFG)
    pd.testing.assert_frame_equal(a, b)


def test_forecast_no_inf():
    df = FF.build_features(_station(), CFG)
    assert not np.isinf(df.select_dtypes("number").to_numpy()).any()


def test_target_column_removed_from_features():
    df = FF.build_features(_station(), CFG)
    cols = FF.feature_columns(df)
    assert "total_flow_m3_min" not in cols
    assert "target" not in cols


# ----------------------------- health ----------------------------- #
def test_guard_div_returns_nan_on_small_denom():
    num = pd.Series([10.0, 10.0])
    den = pd.Series([0.0, 2.0])
    out = HF._guard_div(num, den)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(5.0)


def test_load_band_boundaries():
    lr = pd.Series([0.1, 0.5, 0.9])
    band = HF._load_band(lr, {"low_upper": 0.3, "high_lower": 0.7})
    assert list(band) == ["low", "mid", "high"]


TEMP_COLS = list(HF.TEMP_EXTRA.values())
PRESS_COLS = list(HF.PRESS_EXTRA.values())


def _make_health_inputs(tmp_path: Path):
    raw = tmp_path / "raw"
    proc = tmp_path / "proc"
    raw.mkdir(parents=True)
    proc.mkdir(parents=True)
    times = pd.date_range("2026-04-01 00:00:00", periods=6, freq="1min")

    # 原始状态监测(仅温度)
    st = {"time": times}
    for d in (4, 5):
        for c in TEMP_COLS:
            st[f"{d}#{c}"] = np.arange(6, dtype=float) + d
    pd.DataFrame(st).to_csv(raw / "设备状态监测.csv", index=False, encoding="utf-8-sig")
    # 原始运行参数(仅压力)
    op = {"time": times}
    for d in (4, 5):
        for c in PRESS_COLS:
            op[f"{d}#{c}"] = np.full(6, 0.2)
    pd.DataFrame(op).to_csv(raw / "设备运行参数.csv", index=False, encoding="utf-8-sig")

    # 设备标准表
    rows = []
    for d in (4, 5):
        for i, t in enumerate(times):
            rows.append({
                "time": t, "device_id": d,
                "running": (i % 2 == 0),           # 交替停/开, 用于验证停机隔离
                "load_ratio": 0.5 if i % 2 == 0 else 0.0,
                "power_kw": 1000.0 if i % 2 == 0 else 0.0,
                "flow_m3_min": 200.0 if i % 2 == 0 else 0.0,
                "current_proxy_a": 80.0,
                "vibration_stage_1_mm_s": 4.0 + i * 0.1,
                "vibration_stage_2_mm_s": 3.0,
                "vibration_stage_3_mm_s": 5.0,
                "discharge_temperature_c": 90.0 + i,
            })
    pd.DataFrame(rows).to_parquet(proc / "device_timeseries.parquet", index=False)
    return raw, proc


def test_health_build_small_no_inf_and_stopped_nan(tmp_path):
    raw, proc = _make_health_inputs(tmp_path)
    cfg = {"devices": [4, 5], "load_bands": {"low_upper": 0.3, "high_lower": 0.7},
           "baseline_range": {"start": "2026-01-01 00:00:00", "end": "2026-12-31 00:00:00"}}
    f = HF.build_health_features(raw, proc, cfg)
    assert not np.isinf(f.select_dtypes("number").to_numpy()).any()
    dev_cols = [c for c in f.columns if c.startswith("dev_")]
    stopped = ~f["running"].astype(bool)
    assert len(dev_cols) > 0
    assert f.loc[stopped, dev_cols].isna().all().all()
    # 单位流量功率: 停机(flow=0)应为 NaN
    assert f.loc[stopped, "unit_flow_power"].isna().all()


def test_health_idempotent(tmp_path):
    raw, proc = _make_health_inputs(tmp_path)
    cfg = {"devices": [4, 5], "load_bands": {"low_upper": 0.3, "high_lower": 0.7},
           "baseline_range": {"start": "2026-01-01 00:00:00", "end": "2026-12-31 00:00:00"}}
    a = HF.build_health_features(raw, proc, cfg)
    b = HF.build_health_features(raw, proc, cfg)
    pd.testing.assert_frame_equal(a, b)
