"""B6 新增调度模式 joint_nompc 的兼容性/安全测试 (additive; 三种旧模式行为不变)。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import b_api
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
HAVE_A6 = (HB / "forecast_validation_dispatch_calibrated.parquet").exists()

REQUIRED_KEYS = {"mode", "status", "device_actions", "total_flow_m3_min",
                 "estimated_power_kw", "reason_codes", "latency_ms", "is_fallback",
                 "execution_permission"}


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
    dev, st = frames
    return PIPE.snapshot_from_frames(dev, st, t)


def _t(bundle):
    for t in sorted(bundle.by_issue):
        if all(h in bundle.by_issue[t] for h in (15, 30, 45, 60)):
            return t
    pytest.skip("无完整预测时刻")


def test_modes_include_joint_nompc_and_legacy():
    assert "joint_nompc" in API.MODES
    for m in ("b3", "balanced", "load_balanced"):
        assert m in API.MODES          # 旧模式保留
    assert API.DEFAULT_MODE == "b3"    # 默认不变


def test_joint_nompc_schema_and_permission(bundle, frames):
    t = _t(bundle); snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    out = API.plan_dispatch("joint_nompc", snap, bundle.forecast_result(t, 15), [], con)
    assert out["mode"] == "joint_nompc"
    assert REQUIRED_KEYS.issubset(out.keys())
    assert out["execution_permission"] == "simulation_candidate_only"
    assert b_api.execution_allowed(out) is False
    if out["status"] == C.STATUS_SUCCESS and not out.get("is_fallback"):
        assert b_api.is_success(out) is True
        assert b_api.candidate_usable(out) is True
        assert out["total_flow_m3_min"] > 0


def test_joint_nompc_infeasible_fallback(bundle, frames):
    t = _t(bundle); snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    out = API.plan_dispatch("joint_nompc", snap, None, [], con, forecast_seq=[1e6] * 4)
    assert out["is_fallback"] is True
    assert out["status"] != C.STATUS_SUCCESS
    assert b_api.execution_allowed(out) is False


def test_joint_nompc_timeout_fallback(bundle, frames):
    t = _t(bundle); snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    out = API.plan_dispatch("joint_nompc", snap, bundle.forecast_result(t, 15), [], con, timeout_ms=0)
    assert out["is_fallback"] is True
    assert out["status"] == "timeout"
    assert b_api.is_fallback_result(out) is True


def test_joint_nompc_missing_forecast_insufficient(bundle, frames):
    t = _t(bundle); snap = _snap(frames, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    out = API.plan_dispatch("joint_nompc", snap, None, [], con)
    assert out["is_fallback"] is True
    assert out["status"] == C.STATUS_INSUFFICIENT_DATA
