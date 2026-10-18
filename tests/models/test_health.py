"""阶段 A5 健康诊断测试: 严格字段集合、UTC时间、枚举、停机过滤、异常。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FEATURES = Path("data/processed/health_features.parquet")
THRESHOLDS = Path("configs/health_thresholds.yaml")
METRICS = Path("artifacts/metrics/health_metrics.json")
REPORT = Path("analysis/reports/phaseA5/health_report.md")

TOP_KEYS = {"status", "validation_mode", "evaluation_time", "devices"}
DEVICE_KEYS = {"device_id", "health_score", "risk_level", "anomaly_score",
               "top_anomaly_factor", "current_value", "normal_range",
               "trend_direction", "suggestion"}
RISK_ENUM = {"正常", "关注", "预警", "严重"}
TREND_ENUM = {"平稳", "改善", "恶化"}
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _need():
    if not FEATURES.exists():
        pytest.skip("[lightweight-no-data] 缺少 health_features.parquet (正式验收前须先跑流水线)")


def test_thresholds_externalized_and_penalty():
    assert THRESHOLDS.exists()
    from air_station.models.health import rules
    th = rules.load_thresholds(THRESHOLDS)
    assert "expert_rules" in th and "levels" in th
    assert rules.penalty_array([10.0], 8.0, 12.0)[0] > rules.penalty_array([10.0], 11.0, 12.0)[0]


def test_top_level_keys_strict():
    _need()
    from air_station.models.health import service
    out = service.health_json()
    assert set(out.keys()) == TOP_KEYS
    assert out["status"] == "success"
    assert out["validation_mode"] == "unsupervised"


def test_device_keys_strict_and_order():
    _need()
    from air_station.models.health import service
    out = service.health_json()
    assert [d["device_id"] for d in out["devices"]] == ["4#", "5#"]
    for d in out["devices"]:
        assert set(d.keys()) == DEVICE_KEYS, f"字段集合不符: {set(d.keys())}"


def test_evaluation_time_utc_z():
    _need()
    from air_station.models.health import service
    out = service.health_json()
    assert UTC_Z.match(out["evaluation_time"]), out["evaluation_time"]


def test_ranges_and_enums():
    _need()
    from air_station.models.health import service
    out = service.health_json()
    for d in out["devices"]:
        assert 0 <= d["health_score"] <= 100
        assert 0.0 <= d["anomaly_score"] <= 1.0
        assert d["risk_level"] in RISK_ENUM
        assert d["trend_direction"] in TREND_ENUM


def test_json_serializable():
    _need()
    from air_station.models.health import service
    s = json.dumps(service.health_json(), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_stopped_filtered_first():
    _need()
    from air_station.models.health import rules, service
    th = rules.load_thresholds()
    df = service._load_features("data/processed", th)
    assert df["running"].astype(bool).all()


def test_missing_features_raises():
    from air_station.models.health import service
    with pytest.raises(FileNotFoundError):
        service.health_json(processed_dir="data/does_not_exist")


def test_metrics_and_report_paths():
    assert METRICS.exists(), "缺少 artifacts/metrics/health_metrics.json (先运行 evaluate)"
    assert REPORT.exists(), "缺少 analysis/reports/phaseA5/health_report.md"
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    assert m["validation_mode"] == "unsupervised"
    assert UTC_Z.match(m["evaluation_time"])
    assert set(m["devices"].keys()) == {"4#", "5#"}
    # 负分离度如实保留(不得删改)
    for dv, v in m["devices"].items():
        assert "separation" in v
