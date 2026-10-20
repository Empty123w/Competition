"""C 模块接入接口 (dispatch_api) 集成测试: 两种模式 + 安全回退 + 超时 + 时延。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
HAVE_A6 = (HB / "forecast_validation_dispatch_calibrated.parquet").exists()


@pytest.fixture(scope="module")
def bundle():
    if not HAVE_A6:
        pytest.skip("A6 交接文件缺失")
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    return load_forecast_bundle(HB / "forecast_validation_dispatch_calibrated.parquet",
                                scoring=scor, scoring_split="valid")


@pytest.fixture(scope="module")
def frames():
    return PIPE.load_processed()


def _snap(frames, t):
    device_df, station_df = frames
    return PIPE.snapshot_from_frames(device_df, station_df, t)


def _t(bundle, frames):
    _, station_df = frames
    for t in sorted(bundle.by_issue):
        if all(h in bundle.by_issue[t] for h in (15, 30, 45, 60)):
            return t
    pytest.skip("无完整 15/30/45/60 预测时刻")


REQUIRED_KEYS = {"mode", "status", "device_actions", "total_flow_m3_min",
                 "estimated_power_kw", "reason_codes", "latency_ms", "is_fallback",
                 "execution_permission"}


@pytest.mark.parametrize("mode", ["b3", "balanced", "load_balanced"])
def test_plan_dispatch_schema(bundle, frames, mode):
    t = _t(bundle, frames)
    snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    fr = bundle.forecast_result(t, 15)
    out = API.plan_dispatch(mode, snap, fr, [], con)
    assert out["mode"] == mode
    assert REQUIRED_KEYS.issubset(out.keys())
    assert isinstance(out["device_actions"], list) and out["device_actions"]
    assert out["latency_ms"] >= 0.0
    for a in out["device_actions"]:
        assert a["device_id"] in C.DEVICE_IDS
        assert a["target_flow_m3_min"] is None or a["target_flow_m3_min"] >= 0.0


def test_invalid_mode_raises(bundle, frames):
    snap = _snap(frames, _t(bundle, frames))
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    with pytest.raises(ValueError):
        API.plan_dispatch("nope", snap, None, [], con)


def test_timeout_triggers_fallback(bundle, frames):
    t = _t(bundle, frames)
    snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    fr = bundle.forecast_result(t, 15)
    out = API.plan_dispatch("balanced", snap, fr, [], con, timeout_ms=-1.0)
    assert out["is_fallback"] is True
    assert out["status"] != C.STATUS_SUCCESS
    assert out["execution_permission"] == "simulation_candidate_only"


def test_missing_forecast_fallback_balanced(bundle, frames):
    snap = _snap(frames, _t(bundle, frames))
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    out = API.plan_dispatch("balanced", snap, None, [], con)
    assert out["is_fallback"] is True
