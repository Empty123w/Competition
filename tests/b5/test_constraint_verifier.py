"""独立约束验证器测试 (P0 任务三)。

用与 plan() 不同的验证路径复核未来整段序列: 最短启停、滚动切换、过渡供气、容量与功率。
另验证 plan/plan_v2 对不可行序列的拒绝。
"""
from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import pytest

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config

ROOT = Path(__file__).resolve().parents[2]
_v = importlib.util.spec_from_file_location("constraint_verifier", ROOT / "analysis" / "scripts" / "constraint_verifier.py")
CV = importlib.util.module_from_spec(_v); _v.loader.exec_module(CV)
_m = importlib.util.spec_from_file_location("ms_v1", ROOT / "analysis" / "scripts" / "multistep_optimization.py")
MS = importlib.util.module_from_spec(_m); _m.loader.exec_module(MS)

MODEL = load_optimization_config()["paths"]["power_model"]


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps(max_flow=268.9):
    return {d: CONS.DeviceCapability(d, True, False, 120.0, max_flow, 1.0, "normal", True, True)
            for d in ("4#", "5#")}


def _est(d, q):
    r = PERF.estimate_power(d, float(q), model_path=MODEL)
    return r["estimated_power_kw"] if r["status"] == "success" else None


def test_plan_output_passes_independent_verifier():
    con, caps = _con(), _caps()
    reqs = [[150.0] * 4, [300.0] * 4, [150.0, 300.0, 150.0, 300.0], [250.0, 260.0, 255.0, 240.0]]
    for req in reqs:
        best = MS.plan(req, caps, con, MODEL, {"4#"}, {}, "2026-08-01 00:00:00", 1600.0)
        if best is None:
            continue
        v = CV.verify_sequence(best["seq"], req, caps, con, {"4#"}, {}, "2026-08-01 00:00:00",
                               power_cap=1600.0 )
        assert v == [], f"plan 输出未通过独立验证: {v}"


def test_min_run_violation_detected():
    con, caps = _con(), _caps()
    con = dataclasses.replace(con, min_run_minutes=45)   # > STEP 使相邻步切换违规
    seq = [("4#",), ()]            # 第 0 步开 4#(已运行), 第 1 步立即停 (elapsed=15<45)
    v = CV.verify_sequence(seq, [150.0, 150.0], caps, con, {"4#"},
                           {"4#": "2026-08-01 00:00:00"}, "2026-08-01 00:00:00" )
    assert any(x["code"] == "MIN_RUN_VIOLATION" for x in v)


def test_switch_budget_violation_detected():
    con, caps = _con(), _caps()
    con = dataclasses.replace(con, max_switches_per_hour=1)
    # 交替切换 -> 60min 窗内多次切换
    seq = [(), ("4#",), (), ("4#",)]
    v = CV.verify_sequence(seq, [0.0, 150.0, 0.0, 150.0], caps, con, set(), {},
                           "2026-08-01 00:00:00" )
    assert any(x["code"] == "SWITCH_BUDGET_EXCEEDED" for x in v)


def test_transition_gap_detected():
    con, caps = _con(), _caps()
    # 停机态直接要求双机 -> 首机启动后容量不足
    v = CV.verify_sequence([("4#", "5#")], [300.0], caps, con, set(), {},
                           "2026-08-01 00:00:00" )
    assert any(x["code"] == "TRANSITION_CAPACITY_GAP" for x in v)


def test_capacity_violation_detected():
    con, caps = _con(), _caps()
    v = CV.verify_sequence([("4#",)], [500.0], caps, con, {"4#"}, {},
                           "2026-08-01 00:00:00" )
    assert any(x["code"] == "INFEASIBLE_CAPACITY" for x in v)


def test_plan_rejects_infeasible_never_returns_bad_seq():
    con, caps = _con(), _caps()
    # 需求远超双机 -> plan 返回 None (拒绝)
    assert MS.plan([999.0] * 4, caps, con, MODEL, set(), {}, "2026-08-01 00:00:00", 1600.0) is None


def test_plan_first_step_matches_execution():
    """计划序列首步与实际执行(alloc0/actions)一致。"""
    con, caps = _con(), _caps()
    req = [300.0, 300.0, 300.0, 300.0]
    best = MS.plan(req, caps, con, MODEL, {"4#"}, {}, "2026-08-01 00:00:00", 1600.0)
    assert best is not None
    combo0 = set(best["seq"][0])
    # alloc0 的运行机组应与首步组合一致, 且供气满足首步需求
    assert set(best["alloc0"]["flows"].keys()) == combo0
    assert best["alloc0"]["total_flow_m3min"] + 1e-6 >= req[0]
    acts = MS._actions(best["seq"][0], best["alloc0"], caps, {"4#"}, {"4#": 120.0, "5#": 0.0})
    assert {a.device_id for a in acts if a.running_after} == combo0


def test_plan_v2_output_passes_independent_verifier():
    _v2 = importlib.util.spec_from_file_location("ms_v2", ROOT / "analysis" / "scripts" / "multistep_optimization_v2.py")
    MS2 = importlib.util.module_from_spec(_v2); _v2.loader.exec_module(MS2)
    con, caps = _con(), _caps()

    def est(d, q):
        r = PERF.estimate_power(d, float(q), model_path=MODEL)
        return r["estimated_power_kw"] if r["status"] == "success" else None
    for req in ([150.0] * 4, [300.0] * 4, [150.0, 300.0, 250.0, 260.0]):
        best = MS2.plan_v2(req, caps, con, est, {"4#"}, {}, "2026-08-01 00:00:00", 1600.0, 0.0, MS.SWITCH_PENALTY_KWH)
        if best is None:
            continue
        v = CV.verify_sequence(best["seq"], req, caps, con, {"4#"}, {}, "2026-08-01 00:00:00", power_cap=1600.0)
        assert v == [], f"plan_v2 未通过独立验证: {v}"
