"""阶段 A5 健康诊断测试: 接口schema、停机过滤、阈值外部化、产物存在。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

FEATURES = Path("data/processed/health_features.parquet")
THRESHOLDS = Path("configs/health_thresholds.yaml")
METRICS = Path("artifacts/metrics/health_metrics.json")
REPORT = Path("analysis/reports/phaseA5/health_report.md")

DEVICE_KEYS = ["device_id", "health_score", "risk_level", "anomaly_score",
               "top_anomaly_factor", "current_value", "normal_range",
               "trend_direction", "suggestion"]


def test_thresholds_externalized_file_exists():
    assert THRESHOLDS.exists()
    from air_station.models.health import rules
    th = rules.load_thresholds(THRESHOLDS)
    assert "expert_rules" in th and "levels" in th


def test_penalty_monotonic_and_external():
    from air_station.models.health import rules
    loose = rules.penalty_array([10.0], warn=11.0, alarm=12.0)[0]
    strict = rules.penalty_array([10.0], warn=8.0, alarm=12.0)[0]
    assert strict > loose >= 0.0


def test_interface_schema():
    if not FEATURES.exists():
        pytest.skip("缺少 health_features, 跳过")
    from air_station.models.health import service
    out = service.health_json()
    assert out["status"] == "success"
    assert out["validation_mode"] == "unsupervised"
    assert out["evaluation_time"]
    assert len(out["devices"]) == 2
    for d in out["devices"]:
        for k in DEVICE_KEYS:
            assert k in d, f"缺少字段 {k}"
        assert d["device_id"].endswith("#")
        assert 0 <= d["health_score"] <= 100
        assert 0.0 <= d["anomaly_score"] <= 1.0


def test_stopped_filtered_first():
    if not FEATURES.exists():
        pytest.skip("缺少 health_features, 跳过")
    from air_station.models.health import rules, service
    th = rules.load_thresholds()
    df = service._load_features("data/processed", th)
    assert df["running"].astype(bool).all()


def test_metrics_and_report_exist():
    assert METRICS.exists(), "缺少 health_metrics.json (先运行 evaluate)"
    assert REPORT.exists(), "缺少 health_report.md"
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    assert m["validation_mode"] == "unsupervised"
    assert "devices" in m and len(m["devices"]) == 2
    for dv, v in m["devices"].items():
        for k in ("health_score_latest", "risk_level_latest", "anomaly_rate"):
            assert k in v
