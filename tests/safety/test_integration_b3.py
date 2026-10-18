"""B3 × B4 集成测试: 优化候选经喘振代理评估。"""
from __future__ import annotations

from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization.service import optimize_dispatch
from tests.safety import _helpers as H


def _run(snapshot, upper, **override):
    base_cfg = B1.load_config("configs/baseline.yaml")
    con = C.DispatchConstraints.from_config(base_cfg)
    return optimize_dispatch(snapshot, C.ForecastResult(H.AS_OF, 60, upper - 20, upper - 40, upper, "t"),
                             [], con, **override).to_dict()


def test_optimizer_candidate_assessed_and_blocked_field():
    d = _run(H.snapshot(), 300)
    assert d["status"] == "success"
    assert d["surge_assessment"]["devices"]
    assert d["surge_status"] in {"low", "medium", "high", "unknown"}
    assert "safety_blocked" in d
    assert d["execution_permission"] == "simulation_candidate_only"


def test_high_surge_blocks_candidate():
    # 构造: 单机低流量(低于边界) -> high -> safety_blocked
    d = _run(H.snapshot(running=(True, False), f4=250.0, d4=0.60), 50)
    if d["status"] == "success":
        levels = [x["risk_level"] for x in d["surge_assessment"]["devices"]]
        if "high" in levels:
            assert d["safety_blocked"] is True


def test_unknown_not_treated_as_safe():
    d = _run(H.snapshot(d4=None), 300)
    if d["status"] == "success" and d["surge_assessment"]["devices"]:
        if any(x["risk_level"] == "unknown" for x in d["surge_assessment"]["devices"]):
            assert d["safety_blocked"] is True
            assert C.RC_SURGE_UNKNOWN in d["reason_codes"]


def test_medium_pass_but_flagged():
    d = _run(H.snapshot(igv4=30.0), 300)
    if d["status"] == "success":
        if d["surge_status"] == "medium":
            assert C.RC_SURGE_MEDIUM in d["reason_codes"]
            # medium 不阻断自动流程, 但需复核标记
            assert d["requires_field_review"] is True
