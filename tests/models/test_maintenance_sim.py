"""阶段 A7 预测性维护仿真测试: 三指标达标、可复现、模拟声明。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

M = Path("artifacts/metrics/maintenance_metrics.json")
R = Path("analysis/reports/phaseA7_maint/maintenance_report.md")


def _need():
    if not M.exists():
        pytest.skip("[lightweight-no-data] 缺少 maintenance_metrics.json")


def test_metrics_exist_and_simulated():
    _need()
    d = json.loads(M.read_text(encoding="utf-8"))
    assert d["validation_mode"] in ("simulation", "hybrid_simulation")
    assert d["feature_base"] == "real_telemetry"
    assert d["label_source"] == "injected_degradation"


def test_targets_met():
    _need()
    d = json.loads(M.read_text(encoding="utf-8"))
    assert d["unplanned_stops"]["reduction_pct"] >= 50.0
    assert d["mtbf_hours"]["improvement_pct"] >= 30.0
    assert d["warning_lead_hours"]["median"] >= 24.0
    assert d["warning_accuracy"]["accuracy"] >= 0.90


def test_detection_not_trivial_or_degenerate():
    _need()
    d = json.loads(M.read_text(encoding="utf-8"))
    # 非完美: 仍有少量未预警失效; 且存在误报评估口径(正常工况已计入)
    assert 0.0 < d["warning_accuracy"]["recall"] < 1.0
    assert d["unplanned_stops"]["proposed"] > 0
    assert d["warning_accuracy"]["fp"] >= 0


def test_report_exists():
    _need()
    assert R.exists()
    assert "simulation" in R.read_text(encoding="utf-8")


def test_reproducible():
    from air_station.models.rul import maintenance_sim as MS
    cfg = MS.load_config("configs/maintenance_policy.yaml")
    a = MS.simulate(cfg)["metrics"]
    b = MS.simulate(cfg)["metrics"]
    assert a == b


def test_maintenance_json_schema():
    import re
    from air_station.models.rul.maintenance_sim import maintenance_json
    out = maintenance_json()
    assert set(out.keys()) == {"status", "validation_mode", "evaluation_time",
                               "method", "threshold", "devices"}
    assert out["status"] == "success"
    assert out["validation_mode"] == "hybrid_simulation"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", out["evaluation_time"])
    assert [d["device_id"] for d in out["devices"]] == ["4#", "5#"]
    for d in out["devices"]:
        assert set(d.keys()) == {"device_id", "trend_score", "warning", "risk_level",
                                 "top_trend_factor", "suggestion"}
        assert d["risk_level"] in ("正常", "预警", "未知")
    json.dumps(out, ensure_ascii=False, allow_nan=False)
