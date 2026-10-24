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
    assert d["validation_mode"] == "simulation"
    assert d["origin"] == "simulated"


def test_targets_met():
    _need()
    d = json.loads(M.read_text(encoding="utf-8"))
    assert d["unplanned_stops"]["reduction_pct"] >= 50.0
    assert d["mtbf_hours"]["improvement_pct"] >= 30.0
    assert d["warning_lead_hours"]["median"] >= 24.0


def test_detection_not_trivial_or_degenerate():
    _need()
    d = json.loads(M.read_text(encoding="utf-8"))
    assert 0.0 < d["detection_rate"] < 1.0     # 非完美、非全失败


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
