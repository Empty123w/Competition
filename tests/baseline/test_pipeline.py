"""B1 流水线测试: snapshot 时间边界与回放确定性。"""
from __future__ import annotations

import json

import pandas as pd

from air_station.baseline import pipeline
from tests.baseline import _helpers


def _frames(minutes=12):
    times = pd.date_range("2026-09-11 00:00", periods=minutes, freq="1min")
    station = pd.DataFrame({
        "time": times,
        "header_pressure_mpa": [0.60] * minutes,
        "total_flow_m3_min": [480.0] * minutes,
        "total_power_kw": [2580.0] * minutes,
    })
    device = pd.DataFrame({
        "time": list(times) * 2,
        "device_id": ["4#"] * minutes + ["5#"] * minutes,
        "running": [True] * minutes + [True] * minutes,
        "flow_m3_min": [250.0] * minutes + [230.0] * minutes,
        "power_kw": [1300.0] * minutes + [1280.0] * minutes,
        "discharge_pressure_mpa": [0.58] * minutes + [0.60] * minutes,
        "igv_percent": [100.0] * minutes + [90.0] * minutes,
        "bov_closed_percent": [100.0] * minutes + [100.0] * minutes,
        "quality_valid": [True] * (2 * minutes),
    })
    return device.sort_values(["device_id", "time"]).reset_index(drop=True), station


def test_snapshot_uses_past_only():
    device, station = _frames()
    # 快照时刻在第 3 分钟, 不应看到之后的数据
    snap = pipeline.snapshot_from_frames(device, station, "2026-09-11T00:02:00")
    assert snap.as_of == pd.Timestamp("2026-09-11T00:02:00").isoformat()
    assert snap.header_pressure_mpa == 0.60


def test_run_baseline_from_frames_deterministic():
    device, station = _frames()
    cfg = _helpers.load_base_config()
    times = list(station["time"])
    forecasts = {t: _helpers.make_forecast(upper=480, as_of=t.isoformat()) for t in times}
    r1 = pipeline.run_baseline_from_frames(device, station, forecasts, cfg, step_minutes=3)
    r2 = pipeline.run_baseline_from_frames(device, station, forecasts, cfg, step_minutes=3)
    assert len(r1) == len(r2) == 4
    d1 = [r.to_dict() for r in r1]
    d2 = [r.to_dict() for r in r2]
    for x in d1 + d2:
        x.pop("elapsed_seconds", None)
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    assert all(r.status == "success" for r in r1)
