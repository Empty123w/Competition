"""B3 对外接口 optimize_dispatch 测试。"""
from __future__ import annotations

import json
import time

import pytest

from air_station.optimization import contracts as C
from air_station.optimization.contracts import RulAssessment
from tests.optimization import _b3_helpers as H


def _run(power_model_path, pressure_model_path, **kw):
    args = dict(snapshot_=H.snapshot(), forecast_=H.forecast(300),
                constraints_=H.constraints(), power_model_path=power_model_path,
                pressure_model_path=pressure_model_path)
    args.update(kw)
    return H.run(**args)


# 1. 双机正常运行 --------------------------------------------------- #
def test_normal_both_running(power_model_path, pressure_model_path):
    r = _run(power_model_path, pressure_model_path)
    d = r.to_dict()
    assert d["status"] == "success"
    assert d["target_combination"] == ["4#", "5#"]
    assert d["total_flow_m3_min"] >= d["required_flow_m3_min"] - 1e-6


# 2. 单机能够满足需求 ----------------------------------------------- #
def test_single_machine_selected(power_model_path, pressure_model_path):
    r = _run(power_model_path, pressure_model_path, constraints_=H.constraints(reserve={"mode": "none"}),
             forecast_=H.forecast(200))
    d = r.to_dict()
    assert d["status"] == "success"
    assert len(d["target_combination"]) == 1


# 3. 双机容量仍不足 -> INFEASIBLE + 回退 ---------------------------- #
def test_infeasible_capacity_with_fallback(power_model_path, pressure_model_path):
    r = _run(power_model_path, pressure_model_path,
             constraints_=H.constraints(reserve={"mode": "none"}), forecast_=H.forecast(560))
    d = r.to_dict()
    assert d["status"] == C.STATUS_INFEASIBLE
    assert C.RC_INFEASIBLE_CAPACITY in d["reason_codes"]
    assert d["fallback_candidate"] is not None
    assert d["device_actions"] == []


# 4. 备用约束不允许停机 -------------------------------------------- #
def test_reserve_prevents_single_stop(power_model_path, pressure_model_path):
    r = _run(power_model_path, pressure_model_path, snapshot_=H.snapshot(running=(True, False)),
             constraints_=H.constraints(reserve={"mode": "extra", "value": 0.05, "unit": "percent"}),
             forecast_=H.forecast(300))
    d = r.to_dict()
    assert d["status"] == "success"
    assert d["target_combination"] == ["4#", "5#"]        # 5% 备用使单机不满足 -> 需双机


# 5/11. 切换过渡供气 (服务层无中断) -------------------------------- #
def test_transition_plan_present(power_model_path, pressure_model_path):
    r = _run(power_model_path, pressure_model_path,
             constraints_=H.constraints(reserve={"mode": "none"}), forecast_=H.forecast(200))
    d = r.to_dict()
    # 由双机切到单机 -> 有停机步骤, 且每步 supply_ok
    assert d["transition_plan"]
    assert all(s["supply_ok"] for s in d["transition_plan"])


# 6. 健康限载/设备不可用 ------------------------------------------- #
def test_health_derate_makes_device_unavailable(power_model_path, pressure_model_path):
    con = H.constraints(health={"mode": "derate", "status": "simulation",
                                "derate_map": {"normal": 1.0, "attention": 1.0,
                                               "warning": 0.85, "critical": 0.0}})
    r = _run(power_model_path, pressure_model_path, constraints_=con,
             forecast_=H.forecast(200), health=[H.health("4#", "严重")])
    d = r.to_dict()
    assert d["status"] == "success"
    assert "4#" not in d["target_combination"]


# 7. 模拟 RUL 惩罚开关 --------------------------------------------- #
def test_rul_penalty_toggle_changes_selection(power_model_path, pressure_model_path):
    con = H.constraints(reserve={"mode": "none"})
    off = H.run(H.snapshot(running=(True, True)), H.forecast(200), con,
                power_model_path, pressure_model_path).to_dict()
    rul = [RulAssessment("4#", 100.0), RulAssessment("5#", 900.0)]
    on = H.run(H.snapshot(running=(True, True)), H.forecast(200), con,
               power_model_path, pressure_model_path, rul=rul,
               dispatch={"rul": {"enabled": True, "simulation": True, "threshold_hours": 500.0},
                         "weights": {"rul": 5.0}}).to_dict()
    assert off["target_combination"] == ["4#"]           # 默认按功耗选 4#
    assert on["target_combination"] == ["5#"]            # 开启 RUL 后避开低寿命 4#
    assert C.RC_RUL_SIMULATION in on["reason_codes"]
    assert on["validation_mode"] == "simulation"


# 8. 超出功率模型有效范围 ------------------------------------------ #
def test_out_of_model_range_warning(power_model_path, pressure_model_path):
    con = H.constraints(reserve={"mode": "none"}, capacity={"validated_min_m3min": 50.0})
    r = _run(power_model_path, pressure_model_path, constraints_=con, forecast_=H.forecast(100))
    d = r.to_dict()
    assert any(("外推" in rc) or ("超出" in rc) for rc in d["reason_codes"])


# 9. 压力反事实能力不足 -------------------------------------------- #
def test_pressure_counterfactual_flag(power_model_path, pressure_model_path):
    d = _run(power_model_path, pressure_model_path).to_dict()
    assert C.RC_PRESSURE_COUNTERFACTUAL in d["reason_codes"]
    cap = d["pressure_prediction"].get("capability", {})
    assert "NOT supported" in cap.get("counterfactual_after_dispatch", "")


# 10. 喘振代理 (B4 集成) ------------------------------------------- #
def test_surge_assessed_by_b4(power_model_path, pressure_model_path):
    d = _run(power_model_path, pressure_model_path).to_dict()
    assert d["surge_status"] in {"low", "medium", "high", "unknown"}
    assert d["surge_assessment"] and d["surge_assessment"].get("devices")
    assert C.RC_SURGE_NOT_ASSESSED not in d["reason_codes"]
    assert d["surge_assessment"]["validation_mode"] == "simulation"


def test_surge_disabled_keeps_placeholder(power_model_path, pressure_model_path):
    d = H.run(H.snapshot(), H.forecast(300), H.constraints(), power_model_path,
              pressure_model_path, dispatch={"surge": {"enabled": False}}).to_dict()
    assert d["surge_status"] == "not_assessed_b4"
    assert C.RC_SURGE_NOT_ASSESSED in d["reason_codes"]


# 12. 预测过期 / 缺失 ---------------------------------------------- #
def test_stale_forecast(power_model_path, pressure_model_path):
    d = H.run(H.snapshot(), H.forecast(300, as_of="2026-09-10T00:00:00"), H.constraints(),
              power_model_path, pressure_model_path).to_dict()
    assert d["status"] != "success"
    assert C.RC_STALE_FORECAST in d["reason_codes"]
    assert d["fallback_candidate"] is not None


def test_missing_forecast(power_model_path, pressure_model_path):
    d = H.run(H.snapshot(), None, H.constraints(), power_model_path, pressure_model_path).to_dict()
    assert d["status"] != "success"
    assert C.RC_FORECAST_MISSING_HOLD in d["reason_codes"]


# 13. 超时 --------------------------------------------------------- #
def test_timeout(power_model_path, pressure_model_path):
    d = H.run(H.snapshot(), H.forecast(300), H.constraints(), power_model_path,
              pressure_model_path, dispatch={"timeout_seconds": 0.0}).to_dict()
    assert d["status"] == C.STATUS_TIMEOUT
    assert C.RC_TIMEOUT in d["reason_codes"]
    assert d["device_actions"] == []


# 模型不可用 ------------------------------------------------------- #
def test_model_unavailable(pressure_model_path):
    d = H.run(H.snapshot(), H.forecast(300), H.constraints(),
              "artifacts/models/no_such_power_model.json", pressure_model_path).to_dict()
    assert d["status"] != "success"
    assert C.RC_MODEL_UNAVAILABLE in d["reason_codes"]


# 14. 确定性 ------------------------------------------------------- #
def test_deterministic(power_model_path, pressure_model_path):
    a = _run(power_model_path, pressure_model_path).to_dict()
    b = _run(power_model_path, pressure_model_path).to_dict()
    a.pop("elapsed_seconds"), b.pop("elapsed_seconds")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# 15. 严格 JSON 序列化 --------------------------------------------- #
def test_json_serializable(power_model_path, pressure_model_path):
    s = json.dumps(_run(power_model_path, pressure_model_path).to_dict(),
                   ensure_ascii=False, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


# 17. 来源与 simulation 标记 --------------------------------------- #
def test_validation_mode_and_source_flags(power_model_path, pressure_model_path):
    d = _run(power_model_path, pressure_model_path).to_dict()
    assert d["validation_mode"] == "simulation"
    assert d["source_flags"]
    assert d["execution_permission"] == "simulation_candidate_only"
    assert d["requires_field_review"] is True


# 求解耗时 --------------------------------------------------------- #
def test_solve_time_under_30s(power_model_path, pressure_model_path):
    t0 = time.perf_counter()
    r = _run(power_model_path, pressure_model_path)
    dt = time.perf_counter() - t0
    assert r.status == "success" and dt < 30.0
