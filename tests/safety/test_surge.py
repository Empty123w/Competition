"""B4 喘振风险代理测试。"""
from __future__ import annotations

import json

from air_station.optimization import contracts as C
from air_station.safety import surge as S
from tests.safety import _helpers as H


def _assess(snap, cand, **kw):
    kw.setdefault("boundary_version", "surge-proxy-v0.1")
    return S.assess_surge_risk(snap, cand, **kw)


def test_normal_high_load_low_risk():
    a = _assess(H.snapshot(), H.candidate({"4#": 268.9, "5#": 211.1}))
    assert a.status == "success" and not a.safety_blocked
    assert all(d["risk_level"] == "low" for d in a.devices)


def test_low_flow_below_boundary_high_risk():
    # p=0.60 时工程边界≈69 m3/min, 计划 50 -> margin<0 -> high
    a = _assess(H.snapshot(d4=0.60), H.candidate({"4#": 50.0}))
    d = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert d["risk_level"] == "high" and d["risk_margin"] < 0
    assert a.safety_blocked and C.RC_SURGE_HIGH in a.reason_codes


def test_igv_low_bumps_to_medium():
    a = _assess(H.snapshot(igv4=30.0), H.candidate({"4#": 240.0}))
    d = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert d["risk_level"] == "medium" and "igv_low" in d["trigger_factors"]
    assert C.RC_SURGE_MEDIUM in a.reason_codes


def test_bov_open_bumps_to_medium():
    a = _assess(H.snapshot(bov4=80.0), H.candidate({"4#": 240.0}))
    d = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert d["risk_level"] == "medium" and "bov_open" in d["trigger_factors"]


def test_missing_pressure_is_unknown_not_low():
    snap = H.snapshot(d4=None)
    a = _assess(snap, H.candidate({"4#": 240.0}))
    d = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert d["risk_level"] == "unknown"
    assert a.safety_blocked                     # unknown 不得视为安全通过
    assert C.RC_SURGE_DATA_MISSING in d["reason_codes"]


def test_pressure_out_of_domain_is_unknown():
    a = _assess(H.snapshot(d4=0.90), H.candidate({"4#": 240.0}))
    d = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert d["risk_level"] == "unknown"


def test_boundary_version_mismatch_unknown():
    a = _assess(H.snapshot(), H.candidate({"4#": 240.0}), boundary_version="wrong-version")
    assert all(d["risk_level"] == "unknown" for d in a.devices)
    assert a.safety_blocked


def test_start_transition_factor_recorded():
    a = _assess(H.snapshot(running=(False, True)),
                H.candidate({"4#": 200.0, "5#": 200.0},
                            transition_plan=[{"step": "start", "device_id": "4#"}]))
    d4 = [x for x in a.devices if x["device_id"] == "4#"][0]
    assert "start_transition" in d4["trigger_factors"]


def test_boundary_missing_returns_unknown_blocked(tmp_path):
    a = S.assess_surge_risk(H.snapshot(), H.candidate({"4#": 240.0}),
                            boundary_version="surge-proxy-v0.1",
                            boundary_path=str(tmp_path / "nope.json"))
    assert a.status == "unknown" and a.safety_blocked
    assert C.RC_SURGE_BOUNDARY_UNKNOWN in a.reason_codes


def test_margin_definition_and_json():
    a = _assess(H.snapshot(), H.candidate({"4#": 240.0}))
    d = a.devices[0]
    assert d["margin_definition"] == "(planned_flow - boundary_flow)/boundary_flow"
    s = json.dumps(a.to_dict(), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s
    assert a.validation_mode == "simulation"
    assert a.capability["is_real_surge_line"] is False
