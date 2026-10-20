"""B3 回退候选测试。"""
from __future__ import annotations

from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import fallback as FB
from tests.optimization import _b3_helpers as H


def test_rule_fallback_marked(power_model_path):
    con = H.constraints()
    base_cfg = B1.load_config("configs/baseline.yaml")
    fb = FB.rule_fallback(H.snapshot(), H.forecast(300), [], con,
                          baseline_config=base_cfg, power_model_path=power_model_path)
    r = fb["dispatch_result"]
    assert r.is_fallback is True
    assert r.execution_permission == "simulation_candidate_only"
    assert r.requires_field_review is True
    assert r.objective_components["candidate_type"] == "rule_fallback"


def test_fallback_recheck_passed(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    base_cfg = B1.load_config("configs/baseline.yaml")
    fb = FB.rule_fallback(H.snapshot(), H.forecast(300), [], con,
                          baseline_config=base_cfg, power_model_path=power_model_path,
                          required=300.0)
    assert "checks" in fb["recheck"]
    assert isinstance(fb["recheck"]["passed"], bool)


def test_baseline_candidate_power(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    base_cfg = B1.load_config("configs/baseline.yaml")
    bc = FB.baseline_candidate_power(H.snapshot(), H.forecast(300), [], con,
                                     baseline_config=base_cfg, power_model_path=power_model_path)
    assert bc["status"] == C.STATUS_SUCCESS
    assert bc["estimated_power_kw"] is not None and bc["estimated_power_kw"] > 0
