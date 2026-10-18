"""B1 配置来源与未确认参数追溯测试 (T18)。"""
from __future__ import annotations

from air_station.baseline import rules
from tests.baseline import _helpers


def test_t18_config_sources_traceable():
    cfg = _helpers.load_base_config()
    # 关键参数块必须带 source 标记
    for block in ("demand", "reserve", "pressure", "switching", "health"):
        assert "source" in cfg[block], f"{block} 缺少 source 标记"
    assert "source_flags" in cfg["capacity"]

    con = _helpers.constraints_from(cfg)
    assert con.source_flags, "source_flags 不应为空"
    # 未冻结参数 -> validation_mode 必须是 simulation
    assert con.applied_validation_mode() == "simulation"

    # 额定与有效上限必须分开, 且不把实测瞬时最大值当持续容量
    assert cfg["capacity"]["rated_capacity_m3min"] == 268.9
    assert cfg["capacity"]["validated_operating_max_m3min"] <= cfg["capacity"]["rated_capacity_m3min"]
    assert cfg["capacity"]["observed_max_m3min"] > cfg["capacity"]["rated_capacity_m3min"]


def test_t18_missing_config_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        rules.load_config("configs/does_not_exist_baseline.yaml")
