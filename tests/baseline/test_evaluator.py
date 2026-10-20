"""B1 评估器测试 (T19): 规则基线与历史实测轨迹命名分离。"""
from __future__ import annotations

import pandas as pd

from air_station.baseline import evaluator
from air_station.optimization.contracts import (
    DeviceAction, DispatchResult,
)
from tests.baseline import _helpers


def _frames():
    times = pd.date_range("2026-09-11 00:00", periods=6, freq="1min")
    station = pd.DataFrame({
        "time": times,
        "header_pressure_mpa": [0.56, 0.57, 0.58, 0.0, 0.57, 0.58],
        "total_flow_m3_min": [400, 410, 420, 0, 405, 415],
        "total_power_kw": [2400, 2450, 2500, 0, 2420, 2480],
    })
    device = pd.DataFrame({
        "time": list(times) * 2,
        "device_id": ["4#"] * 6 + ["5#"] * 6,
        "running": [1, 1, 1, 1, 1, 1] + [0, 0, 1, 1, 1, 1],
        "flow_m3_min": [250, 250, 250, 250, 250, 250] + [0, 0, 240, 240, 240, 240],
        "power_kw": [1300] * 6 + [0, 0, 1280, 1280, 1280, 1280],
        "load_ratio": [0.93] * 6 + [0, 0, 0.89, 0.89, 0.89, 0.89],
    })
    return device, station


def test_t19_observed_vs_rule_baseline_named():
    device, station = _frames()
    cfg = _helpers.load_base_config()
    obs = evaluator.observed_metrics(device, station, cfg)
    assert obs["source"] == "observed_operation"
    # 压力为 0 的掉点被剔除
    assert obs["station"]["pressure_compliance_rate"] is not None

    results = [
        DispatchResult(status="success", as_of="2026-09-11T00:00:00",
                       policy_version="baseline-rule-v0.1", validation_mode="simulation",
                       reason_codes=["HOLD"], total_flow_m3_min=480.0,
                       device_actions=[
                           DeviceAction("4#", "adjust", 250.0, 0.93, True),
                           DeviceAction("5#", "start", 230.0, 0.86, True)],
                       required_flow_m3_min=480.0),
        DispatchResult(status="INFEASIBLE", as_of="2026-09-11T00:01:00",
                       policy_version="baseline-rule-v0.1", validation_mode="simulation",
                       reason_codes=["INFEASIBLE_CAPACITY"]),
    ]
    base = evaluator.baseline_metrics(results, cfg)
    assert base["source"] == "manual_rule_baseline"
    assert base["planned_starts"] == 1
    assert base["infeasible_steps"] == 1

    cmp = evaluator.compare_heads_up(base, obs)
    assert set(cmp.keys()) == {"manual_rule_baseline", "observed_operation"}


def test_t19_switch_count_merges_glitches():
    values = [1, 1, 0, 1, 1, 1, 1]        # 中间的 0 是单点毛刺
    assert evaluator.count_switches(values, min_segment_minutes=5) == 0
    values2 = [1, 1, 1, 0, 0, 0, 0, 0, 0]
    assert evaluator.count_switches(values2, min_segment_minutes=5) == 1
