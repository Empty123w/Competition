"""B4 代理边界测试。"""
from __future__ import annotations

import json
from pathlib import Path

from air_station.safety import boundaries as BD


def test_engineering_boundary_monotonic():
    cfg = BD.load_config()
    q_lo, _ = BD.engineering_boundary_flow(0.55, cfg)
    q_hi, _ = BD.engineering_boundary_flow(0.66, cfg)
    assert q_hi > q_lo >= 0.0


def test_engineering_boundary_below_validated_min():
    cfg = BD.load_config()
    q, src = BD.engineering_boundary_flow(0.66, cfg)
    assert q < 120.0        # 边界应低于 validated_min, 否则常态运行即高危
    assert src == "engineering"


def test_build_boundaries_artifact(tmp_path):
    out = tmp_path / "surge_boundary.json"
    art = BD.build_boundaries(out_path=out)
    assert art["boundary_version"] and art["validation_mode"] == "simulation"
    assert "非真实喘振线" in art["note"]
    for did, d in art["devices"].items():
        assert d["knots"], f"{did} 无结点"
        for k in d["knots"]:
            assert k["flow_m3min"] > 0 and k["n"] > 0
    # 可重新读取
    reloaded = BD.load_boundary_artifact(out)
    assert reloaded["devices"].keys() == art["devices"].keys()


def test_boundary_flow_historical_source():
    cfg = BD.load_config()
    art = BD.load_boundary_artifact()
    q, src = BD.boundary_flow("5#", 0.60, cfg, art, source="historical_envelope")
    assert src == "historical_envelope" and q is not None and q > 0


def test_boundary_missing_artifact_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        BD.load_boundary_artifact("artifacts/models/no_such_boundary.json")
