"""三模式状态一致性 / 回退 / 安全阻断 与统一判定规则测试 (B6 接口核查)。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import b_api, contracts as C
from air_station.optimization.contracts import ForecastResult
from air_station.optimization.forecast_adapter import load_forecast_bundle

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
HAVE = (HB / "forecast_validation_dispatch_calibrated.parquet").exists()
MODES = ("b3", "balanced", "load_balanced")


@pytest.fixture(scope="module")
def bundle():
    if not HAVE:
        pytest.skip("A6 交接文件缺失")
    scor = pd.read_parquet(HB / "forecast_scoring_actuals_calibrated.parquet")
    return load_forecast_bundle(HB / "forecast_validation_dispatch_calibrated.parquet",
                                scoring=scor, scoring_split="valid")


@pytest.fixture(scope="module")
def ctx(bundle):
    dev, st = PIPE.load_processed()
    t = next(t for t in sorted(bundle.by_issue) if all(h in bundle.by_issue[t] for h in (15, 30, 45, 60)))
    snap = PIPE.snapshot_from_frames(dev, st, t)
    con = C.DispatchConstraints.from_config(B1.load_config("configs/baseline.yaml"))
    return t, snap, con, bundle.forecast_result(t, 15)


@pytest.mark.parametrize("mode", MODES)
def test_normal_success_flags(mode, ctx):
    t, snap, con, fr = ctx
    out = b_api.plan_dispatch(mode, snap, fr, [], con)
    assert b_api.is_success(out) and b_api.candidate_usable(out)
    assert not b_api.is_fallback_result(out) and not b_api.is_blocked(out)
    assert out["execution_permission"] == "simulation_candidate_only"
    assert b_api.execution_allowed(out) is False
    assert b_api.needs_human_review(out) is False


@pytest.mark.parametrize("mode", MODES)
def test_overcapacity_infeasible(mode, ctx):
    t, snap, con, fr = ctx
    big = ForecastResult(t.isoformat(), 15, 700.0, 650.0, 760.0, "t")
    out = b_api.plan_dispatch(mode, snap, big, [], con)
    assert b_api.is_infeasible(out), out["status"]
    assert b_api.is_fallback_result(out) and not b_api.is_success(out)
    assert b_api.candidate_usable(out) is False and b_api.needs_human_review(out) is True


@pytest.mark.parametrize("mode", MODES)
def test_missing_forecast_is_fallback(mode, ctx):
    t, snap, con, fr = ctx
    out = b_api.plan_dispatch(mode, snap, None, [], con)
    assert b_api.is_fallback_result(out) and not b_api.is_success(out)
    assert b_api.candidate_usable(out) is False


@pytest.mark.parametrize("mode", MODES)
def test_timeout_honored_all_modes(mode, ctx):
    t, snap, con, fr = ctx
    out = b_api.plan_dispatch(mode, snap, fr, [], con, timeout_ms=-1.0)
    assert b_api.is_fallback_result(out), (mode, out.get("status"))
    assert str(out.get("status")).lower() != "success"
    assert C.RC_TIMEOUT in out.get("reason_codes", [])


def test_invalid_mode_raises(ctx):
    t, snap, con, fr = ctx
    with pytest.raises(ValueError):
        b_api.plan_dispatch("nope", snap, fr, [], con)


def test_status_helpers_case_insensitive():
    assert b_api.is_success({"status": "success", "is_fallback": False})
    assert b_api.is_success({"status": "SUCCESS", "is_fallback": False})
    assert not b_api.is_success({"status": "success", "is_fallback": True})
    assert b_api.is_infeasible({"status": "INFEASIBLE"})
    assert b_api.is_infeasible({"status": "infeasible"})


def test_blocked_candidate_not_usable():
    out = {"status": "success", "is_fallback": False, "safety_blocked": True}
    assert b_api.is_blocked(out) and not b_api.candidate_usable(out)
    assert b_api.needs_human_review(out) is True
    assert b_api.execution_allowed(out) is False


def test_high_surge_candidate_blocks(ctx):
    from air_station.optimization.contracts import DeviceAction, DeviceSnapshot, DispatchResult, StationSnapshot
    t, snap, con, fr = ctx
    low = StationSnapshot(t.isoformat(), 0.60,
                          [DeviceSnapshot("4#", True, 40.0, None, 0.60),
                           DeviceSnapshot("5#", True, 40.0, None, 0.60)])
    cand = DispatchResult(C.STATUS_SUCCESS, t.isoformat(), "t")
    cand.device_actions = [DeviceAction("4#", "adjust", 40.0, 40.0 / 268.9, True)]
    sa = b_api.assess_surge_risk(low, cand)
    assert sa.safety_blocked is True and sa.counts["high"] >= 1
