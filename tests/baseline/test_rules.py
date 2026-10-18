"""B1 规则引擎测试 (T01~T17, T20)。"""
from __future__ import annotations

import json

from air_station.optimization import contracts as C
from air_station.optimization.contracts import HealthAssessment

from tests.baseline._helpers import (
    decide, deep_merge, make_forecast, make_snapshot, make_state,
)


def _actions(result):
    return {a.device_id: a for a in result.device_actions}


# T01 ---------------------------------------------------------------- #
def test_t01_pressure_low_starts_machine(base_config, constraints):
    snap = make_snapshot(pressure=0.53, running=(True, False))
    fc = make_forecast(upper=200)             # 单机容量足够, 无容量驱动启动
    res = decide(snap, fc, base_config, constraints, make_state(0.53))
    act = _actions(res)[ "5#"]
    assert act.action == "start"
    assert C.RC_PRESSURE_LOW_START in res.reason_codes


# T02 ---------------------------------------------------------------- #
def test_t02_pressure_high_stops_machine(base_config, constraints):
    snap = make_snapshot(pressure=0.67, running=(True, True))
    fc = make_forecast(upper=200)
    res = decide(snap, fc, base_config, constraints, make_state(0.67))
    assert _actions(res)["4#"].action == "stop"
    assert C.RC_PRESSURE_HIGH_STOP in res.reason_codes


# T03 ---------------------------------------------------------------- #
def test_t03_no_switch_inside_band(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    fc = make_forecast(upper=480)
    res = decide(snap, fc, base_config, constraints, make_state(0.60))
    assert all(a.action in ("hold", "adjust") for a in res.device_actions)


# T04 ---------------------------------------------------------------- #
def test_t04_hysteresis_no_reverse(base_config, constraints):
    # 早前 0.54 越界, 当前回到 0.60 带内 -> 不反向切换
    st = make_state(0.60)
    st.pressure_history = [("t0", 0.54), ("t1", 0.54), ("t2", 0.60)]
    snap = make_snapshot(pressure=0.60, running=(True, True))
    res = decide(snap, make_forecast(upper=480), base_config, constraints, st)
    assert all(a.action in ("hold", "adjust") for a in res.device_actions)


# T05 ---------------------------------------------------------------- #
def test_t05_confirm_minutes_debounce(base_config, constraints):
    st = make_state(0.54, samples=1)          # 历史不足 confirm_minutes
    snap = make_snapshot(pressure=0.54, running=(True, True))
    res = decide(snap, make_forecast(upper=200), base_config, constraints, st)
    assert all(a.action in ("hold", "adjust") for a in res.device_actions)
    assert C.RC_DEBOUNCE_INSUFFICIENT in res.reason_codes


# T06 ---------------------------------------------------------------- #
def test_t06_min_run_lock_defers_stop(base_config, constraints):
    st = make_state(0.67)
    st.last_switch_at = {"4#": "2026-09-10T23:55:00", "5#": "2026-09-10T23:55:00"}
    snap = make_snapshot(pressure=0.67, running=(True, True))
    res = decide(snap, make_forecast(upper=200), base_config, constraints, st)
    assert all(a.action != "stop" for a in res.device_actions)
    assert C.RC_MIN_RUN_LOCK in res.reason_codes


# T07 ---------------------------------------------------------------- #
def test_t07_min_stop_lock_blocks_start(base_config, constraints):
    st = make_state(0.53)
    st.last_switch_at = {"5#": "2026-09-10T23:55:00"}
    snap = make_snapshot(pressure=0.53, running=(True, False))
    res = decide(snap, make_forecast(upper=200), base_config, constraints, st)
    assert _actions(res)["5#"].action != "start"
    assert C.RC_MIN_STOP_LOCK in res.reason_codes


# T08 ---------------------------------------------------------------- #
def test_t08_infeasible_capacity(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    fc = make_forecast(upper=560)             # 需求+备用 > 总额定容量
    res = decide(snap, fc, base_config, constraints, make_state(0.60))
    assert res.status == C.STATUS_INFEASIBLE
    assert C.RC_INFEASIBLE_CAPACITY in res.reason_codes
    assert res.device_actions == []


# T09 ---------------------------------------------------------------- #
def test_t09_reserve_prevents_stop(base_config, constraints):
    snap = make_snapshot(pressure=0.67, running=(True, True))
    fc = make_forecast(upper=300)             # 备用要求使单机无法满足 -> 不能停
    res = decide(snap, fc, base_config, constraints, make_state(0.67))
    assert all(a.action != "stop" for a in res.device_actions)


# T10 ---------------------------------------------------------------- #
def test_t10_health_derate_reduces_limit(base_config, constraints):
    cfg = deep_merge(base_config, {"health": {
        "mode": "derate", "status": "simulation",
        "derate_map": {"normal": 1.0, "attention": 1.0, "warning": 0.85, "critical": 1.0}}})
    con = C.DispatchConstraints.from_config(cfg)
    snap = make_snapshot(pressure=0.60, running=(True, True))
    res = decide(snap, make_forecast(upper=300), cfg, con, make_state(0.60),
                 health=[HealthAssessment("4#", "预警")])
    act4 = _actions(res)["4#"]
    assert act4.upper_limit_m3_min is not None
    assert act4.upper_limit_m3_min < con.rated_capacity_m3min
    assert C.RC_HEALTH_DERATE in res.reason_codes


# T11 ---------------------------------------------------------------- #
def test_t11_health_critical_no_auto_stop(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    res = decide(snap, make_forecast(upper=400), base_config, constraints, make_state(0.60),
                 health=[HealthAssessment("4#", "严重")])
    assert _actions(res)["4#"].action != "stop"
    assert C.RC_HEALTH_STOP_REVIEW in res.reason_codes


# T12 ---------------------------------------------------------------- #
def test_t12_missing_pressure_insufficient(base_config, constraints):
    snap = make_snapshot(pressure=None, running=(True, True))
    res = decide(snap, make_forecast(upper=400), base_config, constraints, make_state(0.60))
    assert res.status == C.STATUS_INSUFFICIENT_DATA
    assert res.device_actions == []


# T13 ---------------------------------------------------------------- #
def test_t13_stale_forecast_rejected(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    fc = make_forecast(upper=400, as_of="2026-09-10T00:00:00")   # 过期 24h
    res = decide(snap, fc, base_config, constraints, make_state(0.60))
    assert res.status != C.STATUS_SUCCESS
    assert C.RC_STALE_FORECAST in res.reason_codes
    assert res.device_actions == []


# T14 ---------------------------------------------------------------- #
def test_t14_load_allocation_sums(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True), f4=250, f5=240)
    res = decide(snap, make_forecast(upper=480), base_config, constraints, make_state(0.60))
    flows = sum(a.target_flow_m3_min or 0.0 for a in res.device_actions)
    assert abs(flows - res.total_flow_m3_min) < 1e-6


# T15 ---------------------------------------------------------------- #
def test_t15_off_machine_zero(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    res = decide(snap, make_forecast(upper=200), base_config, constraints, make_state(0.67))
    off = [a for a in res.device_actions if not a.running_after]
    assert off, "应存在停机机组"
    for a in off:
        assert a.target_flow_m3_min == 0.0
        assert a.target_load_ratio == 0.0
        assert a.estimated_power_kw == 0.0


# T16 ---------------------------------------------------------------- #
def test_t16_deterministic_repeat(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    fc = make_forecast(upper=400)
    a = decide(snap, fc, base_config, constraints, make_state(0.60)).to_dict()
    b = decide(snap, fc, base_config, constraints, make_state(0.60)).to_dict()
    a.pop("elapsed_seconds"), b.pop("elapsed_seconds")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# T17 ---------------------------------------------------------------- #
def test_t17_json_serializable_no_nan(base_config, constraints):
    snap = make_snapshot(pressure=0.60, running=(True, True))
    res = decide(snap, make_forecast(upper=400), base_config, constraints, make_state(0.60))
    s = json.dumps(res.to_dict(), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


# T20 ---------------------------------------------------------------- #
def test_t20_only_devices_4_5(base_config, constraints):
    snap3 = make_snapshot(pressure=0.60, devices=("3#", "4#", "5#"))
    res = decide(snap3, make_forecast(upper=400), base_config, constraints, make_state(0.60))
    assert res.status == C.STATUS_INVALID_INPUT
    assert C.RC_INVALID_INPUT in res.reason_codes

    snap_missing = make_snapshot(pressure=0.60, devices=("4#",))
    res2 = decide(snap_missing, make_forecast(upper=400), base_config, constraints, make_state(0.60))
    assert res2.status == C.STATUS_INVALID_INPUT
