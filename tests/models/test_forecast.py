"""阶段 A4 负荷预测测试: 接口结构、时延、加载一致性、指标schema、基线对比。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

MODEL_DIR = Path("artifacts/models")
METRICS = Path("artifacts/metrics/forecast_metrics.json")
PROCESSED = Path("data/processed/forecast_features.parquet")


def _need(*paths):
    if not all(p.exists() for p in paths):
        pytest.skip("缺少训练产物, 跳过 (先运行 train/evaluate)")


def test_interface_structure_and_latency():
    _need(PROCESSED, MODEL_DIR / "forecast_15m.pkl")
    from air_station.models.forecasting import service
    out = service.forecast_json()
    assert out["status"] == "success"
    assert out["model_type"]
    assert "prediction_base_time" in out
    hs = [p["horizon_minutes"] for p in out["predictions"]]
    assert hs == [15, 30, 60]
    for p in out["predictions"]:
        assert p["lower_bound"] <= p["predicted_flow"] <= p["upper_bound"]
    assert out["elapsed_seconds"] <= 10.0


def test_model_reload_consistent():
    _need(PROCESSED, MODEL_DIR / "forecast_15m.pkl")
    import joblib
    import pandas as pd
    feats = pd.read_parquet(PROCESSED)
    last = feats.iloc[[-1]]
    a = joblib.load(MODEL_DIR / "forecast_15m.pkl")
    b = joblib.load(MODEL_DIR / "forecast_15m.pkl")
    pa = a["model"].predict(last[a["feature_cols"]])
    pb = b["model"].predict(last[b["feature_cols"]])
    assert np.allclose(pa, pb)


def test_metrics_schema_and_baseline():
    _need(METRICS)
    d = json.loads(METRICS.read_text(encoding="utf-8"))
    assert "model_type" in d
    for k in ("train_ratio", "val_ratio", "test_ratio"):
        assert k in d["data_split"]
    for h in ("15min", "30min", "60min"):
        m = d["metrics"][h]
        for k in ("MAPE", "MAE", "RMSE", "WAPE"):
            assert k in m and np.isfinite(m[k])
    assert "test_coverage_rate" in d["confidence_interval"]
    assert 0.0 <= d["confidence_interval"]["test_coverage_rate"] <= 1.0
    # 至少优于一个简单基线
    for h in ("15min", "30min", "60min"):
        m = d["metrics"][h]
        bmap = m["baseline_MAPE_all"]
        assert any(m["MAPE"] < v for v in bmap.values() if np.isfinite(v))


def test_mape_target_met():
    _need(METRICS)
    d = json.loads(METRICS.read_text(encoding="utf-8"))
    target = d.get("target_mape_pct", 8.0)
    for h in ("15min", "30min", "60min"):
        assert d["metrics"][h]["MAPE"] <= target
