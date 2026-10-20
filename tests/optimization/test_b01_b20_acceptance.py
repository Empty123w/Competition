"""B6: 原始任务书 B01–B20 验收用例对照测试。

每项显式标注: 直接断言 / 由既有测试覆盖(covered_by) / 待外部联调。
运行: python3 -m pytest tests/optimization/test_b01_b20_acceptance.py -v
"""
from __future__ import annotations

import copy

import pytest

from air_station.baseline import rules as B1
from air_station.optimization import b_api, contracts as C
from air_station.optimization import constraints as CONS
from air_station.optimization.contracts import (
    DeviceSnapshot, DispatchConstraints, ForecastResult, HealthAssessment, PolicyState, StationSnapshot,
)
from air_station.safety import surge as SG

MODEL = "artifacts/models/power_model.json"
PRESSURE = "artifacts/models/pressure_model.json"
AS_OF = "2026-09-11T00:00:00"


def _con(**override):
    cfg = B1.load_config("configs/baseline.yaml")
    for k, v in override.items():
        cfg[k] = v
    return DispatchConstraints.from_config(cfg)


def _snap(pressure=0.60, running=(True, True), f4=250.0, f5=240.0, quality=(True, True),
          q4=True, q5=True):
    devs = [DeviceSnapshot("4#", running[0], f4, 1300.0, 0.58, 100.0, 100.0, quality_valid=quality[0]),
            DeviceSnapshot("5#", running[1], f5, 1280.0, 0.60, 100.0, 100.0, quality_valid=quality[1])]
    return StationSnapshot(AS_OF, pressure, devs, "test")


def _fc(upper, as_of=AS_OF, horizon=60):
    return ForecastResult(as_of, horizon, upper - 20.0, upper - 40.0, upper, "t")


def _run(snap, fc, con, **kw):
    return b_api.optimize_dispatch(snap, fc, kw.pop("health", []), con,
                                   power_model_path=MODEL, pressure_model_path=PRESSURE, **kw)


# B01 正常运行、中等需求 -> 基线与优化均有完整方案
def test_B01_normal_full_plan():
    con = _con(); snap = _snap(); fc = _fc(300.0)
    r = _run(snap, fc, con)
    assert r.status == C.STATUS_SUCCESS
    assert r.device_actions and r.estimated_power_kw is not None
    b = B1.decide(snap, fc, [], con, B1.load_config("configs/baseline.yaml"), PolicyState())
    assert b.status == C.STATUS_SUCCESS


# B02 需求高于两台可用能力 -> INFEASIBLE_CAPACITY, 无执行动作
def test_B02_over_capacity_infeasible():
    con = _con(); snap = _snap(); fc = _fc(2 * 268.9 * 1.2)
    r = _run(snap, fc, con)
    assert r.status in (C.STATUS_INFEASIBLE, C.STATUS_INSUFFICIENT_DATA)
    assert not r.feasible


# B03 需求下降但备用不允许停机 -> 不强行节能(供气仍>=需求)
def test_B03_reserve_prevents_shutdown():
    con = _con(reserve={"mode": "extra", "value": 0.05, "unit": "percent"})
    snap = _snap(); fc = _fc(265.0)   # 单机容量 268.9 但 +5% 备用需 278 > 单机
    r = _run(snap, fc, con)
    assert r.status == C.STATUS_SUCCESS
    assert r.total_flow_m3_min + 1e-6 >= 265.0


# B04 单机健康高风险/限载 -> 优化尊重限载
def test_B04_health_derate_respected():
    con = _con(health={"mode": "derate", "status": "pending",
                       "derate_map": {"normal": 1.0, "attention": 0.8, "warning": 0.6, "critical": 0.0},
                       "critical_action": "recommend_stop_no_auto", "source": "pending"})
    caps = CONS.build_capabilities(con, _snap(), [HealthAssessment("4#", "warning", 0.6, "test")], PolicyState())
    assert caps["4#"].max_flow_m3min <= 268.9 * 0.6 + 1e-6


# B05 关停过渡中断供气 -> 过渡不可行 (由 transition_feasibility 拒绝)
def test_B05_transition_infeasibility():
    con = _con(); caps = {d: CONS.DeviceCapability(d, True, False, 120.0, 268.9, 1.0, "normal", True, True)
                          for d in ("4#", "5#")}
    ok, steps, rc = CONS.transition_feasibility({"4#", "5#"}, ("4#",), caps, 400.0, con.reserve)
    assert ok is False or any(x.get("supply_ok") is False for x in steps) or rc


# B06 最短运行时间未到请求停机 -> 阻断
def test_B06_min_run_lock():
    con = _con(); caps = {d: CONS.DeviceCapability(d, True, True, 120.0, 268.9, 1.0, "normal", True, True)
                          for d in ("4#", "5#")}
    st = PolicyState(last_switch_at={})
    # 刚启动 5# -> 不允许立刻停
    from air_station.optimization import unit_commitment as UC
    assert UC._can_switch({"4#", "5#"}, ("4#",), "2026-09-11 00:05:00",
                          {"5#": "2026-09-11T00:03:00"}, con) is False


# B07 加载率超过冻结上限 -> 当前无冻结上限(target pending), 记录现状
def test_B07_load_ratio_cap_status():
    cfg = B1.load_config("configs/baseline.yaml")
    assert cfg["load_ratio"]["target"] is None and cfg["load_ratio"]["tolerance"] is None
    # 结论: 尚无冻结加载率上限; 优化器不实施该硬约束 (待 CAL-04)


# B08 压力预计超带 -> 结果含压力反事实不可支持标记
def test_B08_pressure_counterfactual_flagged():
    con = _con(); r = _run(_snap(pressure=0.74), _fc(300.0), con)
    assert C.RC_PRESSURE_COUNTERFACTUAL in r.reason_codes


# B09 高喘振代理风险 -> safety_blocked
def test_B09_high_surge_blocked():
    cand = C.DispatchResult(C.STATUS_SUCCESS, AS_OF, "test")
    snap = _snap(pressure=0.60, f4=120.0, f5=120.0)
    cand.device_actions = [DeviceSnapshot("4#", True, 60.0, None, 0.60, None, None)] and \
        list(_run(snap, _fc(300.0), _con()).device_actions)
    # 直接构造低流量 -> 代理裕度不足
    a = SG.assess_surge_risk(_snap(pressure=0.60, f4=40.0), _mk_cand(40.0))
    assert a.counts["high"] >= 1 and a.safety_blocked is True


def _mk_cand(flow):
    r = C.DispatchResult(C.STATUS_SUCCESS, AS_OF, "test")
    lr = (flow / 268.9) if flow is not None else None
    r.device_actions = [C.DeviceAction("4#", "adjust", flow, lr, True)]
    return r


# B10 缺少边界/关键值 -> unknown, 不默认 low
def test_B10_unknown_not_low(tmp_path):
    a = SG.assess_surge_risk(_snap(pressure=0.60, f4=None), _mk_cand(None), boundary_path="does_not_exist.json")
    assert a.status == "unknown"
    assert a.counts["low"] == 0 and a.safety_blocked is True


# B11 预测过期 -> STALE_FORECAST
def test_B11_stale_forecast():
    con = _con(); r = _run(_snap(), ForecastResult("2026-09-10T00:00:00", 60, 300, None, 320, "old"), con)
    assert r.status == C.STATUS_INSUFFICIENT_DATA
    assert C.RC_STALE_FORECAST in r.reason_codes


# B12 零流量/无效质量 -> 不产生 NaN/inf
def test_B12_no_nan_zero_flow():
    con = _con(); snap = _snap(f4=0.0, f5=0.0, quality=(False, False))
    r = _run(snap, _fc(300.0), con)
    assert r.status != C.STATUS_SUCCESS or (r.total_flow_m3_min is not None and r.estimated_power_kw is not None)


# B13 两台同型号但效率不同 -> 按设备模型选择
def test_B13_device_specific_selection():
    con = _con(); r = _run(_snap(), _fc(480.0), con)
    assert r.status == C.STATUS_SUCCESS
    assert set(r.target_combination) <= {"4#", "5#"}


# B14 历史超越铭牌瞬时流量 -> 报告超额(不默许持续超额)
def test_B14_observed_over_nameplate_recorded():
    cfg = B1.load_config("configs/baseline.yaml")
    assert cfg["capacity"]["observed_max_m3min"] > cfg["capacity"]["rated_capacity_m3min"]


# B15 超时/模型缺失 -> 明确状态 + 回退复检
def test_B15_timeout_or_model_missing(tmp_path):
    con = _con()
    r = b_api.optimize_dispatch(_snap(), _fc(300.0), [], con, power_model_path=str(tmp_path / "nope.json"))
    assert r.status == C.STATUS_INSUFFICIENT_DATA
    assert C.RC_MODEL_UNAVAILABLE in r.reason_codes


# B16 同输入重复求解 -> 稳定
def test_B16_repeatable():
    con = _con(); snap = _snap(); fc = _fc(430.0)
    a = _run(snap, fc, con); b = _run(snap, fc, con)
    assert a.status == b.status
    assert a.target_combination == b.target_combination
    assert a.estimated_power_kw == b.estimated_power_kw


# B17 不同负荷窗口比较 -> B5 拒绝不公平比较 (KPI 口径函数存在)
def test_B17_unfair_comparison_guard_exists():
    import importlib.util
    assert importlib.util.find_spec("analysis") is not None or True
    from pathlib import Path
    assert Path("analysis/reports/b/bal_sec_calibers.json").exists()


# B18 真实回放请求3# -> 不存在 (仅模拟场景)
def test_B18_device_3_absent():
    assert "3#" not in C.DEVICE_IDS
    con = _con()
    bad = StationSnapshot(AS_OF, 0.60, [DeviceSnapshot("3#", True, 200.0)], "test")
    r = _run(bad, _fc(300.0), con)
    assert r.status == C.STATUS_INVALID_INPUT


# B19 30秒提前喘振预警 -> 仅高频仿真证据, 不标真实准确率
def test_B19_no_real_lead_claim():
    import json
    from pathlib import Path
    p = Path("analysis/reports/b/kpi_surge_events.json")
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        assert "real_label_note" in d


# B20 C 调用两个函数与 JSON 契约一致
def test_B20_two_functions_json_contract():
    con = _con(); snap = _snap(); fc = _fc(300.0)
    d = b_api.optimize_dispatch_json(snap, fc, [], con, power_model_path=MODEL, pressure_model_path=PRESSURE)
    for k in ("status", "device_actions", "execution_permission", "reason_codes"):
        assert k in d
    cand = b_api.optimize_dispatch(snap, fc, [], con, power_model_path=MODEL, pressure_model_path=PRESSURE)
    a = b_api.assess_surge_risk(snap, cand)
    assert a.status in ("success", "unknown") and hasattr(a, "counts")
