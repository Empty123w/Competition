"""阶段 A6 滚动回放预测测试: 时间对齐、防泄漏、数值质量、B侧表结构。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HANDOFF = Path("artifacts/handoff_b")
AUDIT = Path("artifacts/metrics/forecast_rolling_audit.parquet")
FROZEN = Path("analysis/reports/phaseA6_replay/frozen_artifact_hashes.json")
DISPATCH_COLS = ["as_of", "target_time", "horizon_minutes", "predicted_flow", "lower_bound", "upper_bound"]


def _need():
    if not (HANDOFF / "forecast_dev_dispatch.parquet").exists():
        pytest.skip("[lightweight-no-data] 缺少滚动回放产物")


def test_dispatch_schema_and_primary_key():
    _need()
    for f in ("forecast_dev_dispatch.parquet", "forecast_validation_dispatch.parquet"):
        d = pd.read_parquet(HANDOFF / f)
        assert list(d.columns) == DISPATCH_COLS
        assert not d.duplicated(["as_of", "horizon_minutes"]).any()
        assert set(d["horizon_minutes"].unique()) <= {15, 30, 60}


def test_target_time_alignment():
    _need()
    d = pd.read_parquet(HANDOFF / "forecast_dev_dispatch.parquet")
    delta = (pd.to_datetime(d["target_time"]) - pd.to_datetime(d["as_of"])).dt.total_seconds() / 60
    assert (delta == d["horizon_minutes"]).all()


def test_as_of_on_5min_grid_and_no_interpolation():
    _need()
    d = pd.read_parquet(HANDOFF / "forecast_dev_dispatch.parquet")
    assert (pd.to_datetime(d["as_of"]).dt.minute % 5 == 0).all()


def test_no_actual_in_dispatch_and_scoring_has_flag():
    _need()
    d = pd.read_parquet(HANDOFF / "forecast_dev_dispatch.parquet")
    assert "actual_flow" not in d.columns
    sc = pd.read_parquet(HANDOFF / "forecast_scoring_actuals.parquet")
    assert "actual_flow" in sc.columns and "actual_available" in sc.columns


def test_actual_matches_target_minute():
    _need()
    sc = pd.read_parquet(HANDOFF / "forecast_scoring_actuals.parquet")
    st = pd.read_parquet("data/processed/station_timeseries.parquet")
    st["time"] = pd.to_datetime(st["time"])
    act = st.set_index("time")["total_flow_m3_min"]
    s = sc.sample(min(200, len(sc)), random_state=0)
    exp = s["target_time"].map(act)
    m = exp.notna()
    assert np.allclose(exp[m].to_numpy(), s.loc[m, "actual_flow"].to_numpy(), atol=1e-6)


def test_bounds_and_finite():
    _need()
    d = pd.concat([pd.read_parquet(HANDOFF / "forecast_dev_dispatch.parquet"),
                   pd.read_parquet(HANDOFF / "forecast_validation_dispatch.parquet")])
    m = d["predicted_flow"].notna()
    assert (d.loc[m, "lower_bound"] <= d.loc[m, "predicted_flow"]).all()
    assert (d.loc[m, "predicted_flow"] <= d.loc[m, "upper_bound"]).all()
    assert np.isfinite(d.loc[m, "predicted_flow"]).all()
    assert (d.loc[m, "predicted_flow"] >= 0).all()


def test_no_future_leakage_in_audit():
    if not AUDIT.exists():
        pytest.skip("[lightweight-no-data] 缺少审计表")
    a = pd.read_parquet(AUDIT)
    assert (pd.to_datetime(a["model_train_target_end"]) < pd.to_datetime(a["calibration_start"])).all()
    assert (pd.to_datetime(a["calibration_target_end"]) < pd.to_datetime(a["as_of"])).all()
    assert (pd.to_datetime(a["input_data_cutoff_time"]) <= pd.to_datetime(a["as_of"])).all()


def test_audit_has_no_bulk_hash_columns():
    if not AUDIT.exists():
        pytest.skip("[lightweight-no-data] 缺少审计表")
    a = pd.read_parquet(AUDIT)
    assert "model_sha256" not in a.columns and "config_sha256" not in a.columns
    assert "fold_id" in a.columns
    assert Path("artifacts/metrics/forecast_fold_metadata.json").exists()


def test_validation_marked_rolling_update():
    _need()
    a = pd.read_parquet(AUDIT)
    va = a[pd.to_datetime(a["as_of"]) >= pd.Timestamp("2026-08-01")]
    assert (va["evaluation_mode"] == "rolling_update_evaluation").all()
    assert va["rolling_update"].all()


def test_frozen_artifacts_unchanged():
    if not FROZEN.exists():
        pytest.skip("缺少冻结哈希基线")
    import hashlib
    ref = json.loads(FROZEN.read_text(encoding="utf-8"))["files"]
    targets = ["artifacts/metrics/forecast_test_predictions.parquet",
               "artifacts/models/forecast_15m.pkl",
               "artifacts/models/rul_median.pkl",
               "configs/forecasting.yaml"]
    for f in targets:
        if f not in ref or ref[f] is None:
            continue
        h = hashlib.sha256(Path(f).read_bytes()).hexdigest().upper()
        assert h == ref[f], f"冻结文件被修改: {f}"
