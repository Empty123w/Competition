"""A6 最小集成验证: 固定场景下的时间/字段/单位/质量回退/安全约束行为。

不做全量新算法对照或正式指标重评; 仅验证接口接入与安全回退。
依赖 A6 交接文件; 缺失则跳过。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import health_rul_adapter as HR
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
H = ROOT / "artifacts" / "metrics" / "health_history.parquet"
R = ROOT / "artifacts" / "metrics" / "rul_history.parquet"
HAVE_A6 = (HB / "forecast_validation_dispatch.parquet").exists() and \
    (HB / "forecast_scoring_actuals.parquet").exists()


@pytest.fixture(scope="module")
def bundle():
    if not HAVE_A6:
        pytest.skip("A6 交接文件缺失")
    scor = pd.read_parquet(HB / "forecast_scoring_actuals.parquet")
    return load_forecast_bundle(HB / "forecast_validation_dispatch.parquet",
                                scoring=scor, scoring_split="valid")


@pytest.fixture(scope="module")
def frames():
    return PIPE.load_processed()


def _scenario_times(station_df):
    v = station_df[(station_df["time"] >= "2026-08-01") & (station_df["time"] <= "2026-08-20 23:59:00")]
    v = v[v["total_flow_m3_min"].notna()]
    q = v["total_flow_m3_min"].quantile([0.5, 0.99, 0.05])
    return {"normal": v.iloc[(v["total_flow_m3_min"] - q[0.5]).abs().argmin()]["time"],
            "high": v.iloc[(v["total_flow_m3_min"] - q[0.99]).abs().argmin()]["time"],
            "low": v.iloc[(v["total_flow_m3_min"] - q[0.05]).abs().argmin()]["time"]}


def test_forecast_time_field_unit(bundle):
    ts = pd.Timestamp("2026-08-01 00:00:00")
    fr = bundle.forecast_result(ts, 15)
    assert fr is not None
    assert fr.horizon_minutes == 15
    # target = as_of + horizon; 单位 m3/min 合理范围
    assert 0 < fr.predicted_flow < 2000
    assert fr.lower_bound <= fr.predicted_flow <= fr.upper_bound
    # actual 仅评估, 不作输入
    assert bundle.actual_at_target(ts, 15) is not None


@pytest.mark.parametrize("kind", ["normal", "high", "low"])
def test_dispatch_accepts_a6_inputs(bundle, frames, kind):
    device_df, station_df = frames
    t = _scenario_times(station_df)[kind]
    snap = PIPE.snapshot_from_frames(device_df, station_df, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    fr = bundle.forecast_result(t, 15)
    r = optimize_dispatch(snap, fr, [], con)
    assert r.status in {"success", "INSUFFICIENT_DATA", "INFEASIBLE"}
    assert r.requires_field_review is True
    assert r.execution_permission == "simulation_candidate_only"
    if r.status == "success":
        assert r.total_flow_m3_min and r.total_flow_m3_min > 0
        assert r.estimated_power_kw and r.estimated_power_kw > 0


def test_forecast_missing_conservative_hold(frames):
    device_df, station_df = frames
    t = pd.Timestamp("2026-03-26 00:00:00")
    snap = PIPE.snapshot_from_frames(device_df, station_df, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    r = optimize_dispatch(snap, None, [], con)     # 无预测
    assert r.status == "INSUFFICIENT_DATA"
    assert r.fallback_candidate is not None        # 保守保持


def test_health_and_rul_fallback_paths():
    h = HR.load_history(H)
    r = HR.load_history(R)
    t = "2026-03-26 00:00:00"   # baseline_insufficient -> health invalid
    out = HR.dispatch_health_rul(h, r, "4#", t)
    assert out["risk_level"] == "unknown" and out["fallback"] == "unknown"
    # 某 OOD 时刻 RUL 不可用 -> 回退健康
    r_ood = r[r["out_of_distribution"]].head(1)
    if len(r_ood):
        t2 = str(r_ood.iloc[0]["time"])
        out2 = HR.dispatch_health_rul(h, r, HR.normalize_device_id(r_ood.iloc[0]["device_id"]), t2)
        assert out2["rul_status"] == "ood"


def test_rul_never_relaxes_safety_constraints(bundle, frames):
    """RUL 仅软惩罚: 传入 RUL 不改变备用/安全阈值与执行权限。"""
    device_df, station_df = frames
    t = _scenario_times(station_df)["normal"]
    snap = PIPE.snapshot_from_frames(device_df, station_df, t)
    con = C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))
    fr = bundle.forecast_result(t, 15)
    rul = [C.RulAssessment("4#", 10.0, 5.0, 20.0)]
    r = optimize_dispatch(snap, fr, [], con, rul=rul)
    assert con.reserve.mode == "extra" and abs(con.reserve.value - 0.05) < 1e-9
    assert r.execution_permission == "simulation_candidate_only"
