"""阶段 A4 负荷预测测试: 接口结构、时域、异常、时延、可序列化。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

MODEL_DIR = Path("artifacts/models")
PROCESSED = Path("data/processed/forecast_features.parquet")

TOP_KEYS = {"status", "model_type", "prediction_base_time", "predictions", "elapsed_seconds"}


def _need():
    if not PROCESSED.exists() or not (MODEL_DIR / "forecast_15m.pkl").exists():
        pytest.skip("[lightweight-no-data] 缺少训练产物, 集成测试跳过 (正式验收前须先跑流水线)")


def test_interface_structure_and_default_horizons():
    _need()
    from air_station.models.forecasting import service
    out = service.forecast_json()
    assert set(out.keys()) == TOP_KEYS
    assert out["status"] == "success"
    assert [p["horizon_minutes"] for p in out["predictions"]] == [15, 30, 60]
    for p in out["predictions"]:
        assert set(p.keys()) == {"horizon_minutes", "target_time", "predicted_flow",
                                 "lower_bound", "upper_bound"}
        assert p["lower_bound"] <= p["predicted_flow"] <= p["upper_bound"]
    assert out["elapsed_seconds"] <= 10.0


def test_json_serializable_no_nan():
    _need()
    from air_station.models.forecasting import service
    out = service.forecast_json()
    s = json.dumps(out, ensure_ascii=False, allow_nan=False)   # 断言不含 NaN/Infinity
    assert "NaN" not in s and "Infinity" not in s


def test_unsupported_horizon_raises():
    _need()
    from air_station.models.forecasting import service
    with pytest.raises(ValueError) as ei:
        service.forecast_json(horizons=[99])
    msg = str(ei.value)
    assert "99" in msg and "forecast_99m.pkl" in msg


def test_missing_features_raises():
    from air_station.models.forecasting import service
    with pytest.raises(FileNotFoundError):
        service.forecast_json(processed_dir="data/does_not_exist")


def test_missing_model_raises():
    _need()
    from air_station.models.forecasting import service
    with pytest.raises(FileNotFoundError):
        service.forecast_json(model_dir="artifacts/does_not_exist")


def test_model_reload_consistent():
    _need()
    import joblib
    import pandas as pd
    feats = pd.read_parquet(PROCESSED)
    last = feats.iloc[[-1]]
    a = joblib.load(MODEL_DIR / "forecast_15m.pkl")
    b = joblib.load(MODEL_DIR / "forecast_15m.pkl")
    assert np.allclose(a["model"].predict(last[a["feature_cols"]]),
                       b["model"].predict(last[b["feature_cols"]]))


def test_metrics_schema_and_baseline():
    metrics = Path("artifacts/metrics/forecast_metrics.json")
    if not metrics.exists():
        pytest.skip("[lightweight-no-data] 缺少 forecast_metrics.json")
    d = json.loads(metrics.read_text(encoding="utf-8"))
    for k in ("train_ratio", "val_ratio", "test_ratio"):
        assert k in d["data_split"]
    for h in ("15min", "30min", "60min"):
        m = d["metrics"][h]
        for k in ("MAPE", "MAE", "RMSE", "WAPE"):
            assert k in m and np.isfinite(m[k])
        bmap = m["baseline_MAPE_all"]
        assert any(m["MAPE"] < v for v in bmap.values() if np.isfinite(v))
    assert 0.0 <= d["confidence_interval"]["test_coverage_rate"] <= 1.0
    assert all(d["metrics"][h]["MAPE"] <= d.get("target_mape_pct", 8.0)
               for h in ("15min", "30min", "60min"))
