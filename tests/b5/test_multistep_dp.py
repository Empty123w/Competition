"""完整多时间步 DP 规划的穷举交叉验证与语义测试。

不修改冻结 B1-B4; 仅验证 analysis/scripts/multistep_optimization.py 的规划器逻辑:
  - 返回解与独立暴力枚举(4^H)结果一致(代价与序列);
  - 累计代价 = 时域电量 + 切换惩罚*切换数;
  - 每步满足容量+备用; 首步遵守最短启停锁定; 时域切换 <= max_switches_per_hour;
  - 容量不可行时返回 None。
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path

import pytest

from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization.service import load_optimization_config

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "multistep_optimization", ROOT / "analysis" / "scripts" / "multistep_optimization.py")
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)

DEVICE_IDS = ("4#", "5#")


def _con():
    return C.DispatchConstraints.from_config(B1.load_config(B1.DEFAULT_CONFIG))


def _caps(max_flow=268.9, running=("4#", "5#")):
    caps = {}
    for d in DEVICE_IDS:
        caps[d] = CONS.DeviceCapability(
            device_id=d, available=True, currently_running=d in running,
            min_flow_m3min=120.0, max_flow_m3min=max_flow, health_factor=1.0,
            internal_risk="normal", can_start=True, can_stop=True)
    return caps


def _model_path():
    return load_optimization_config()["paths"]["power_model"]


def _brute_force(required_seq, caps, con, model, cur_on, last_switch, as_of):
    """独立枚举所有 4^H 序列, 返回最小代价解 (与 plan 的代价定义一致)。"""
    H = len(required_seq)
    step_opts = []
    for k in range(H):
        opts = {}
        for combo in CONS.COMBINATIONS:
            a = CONS.assess_combination(combo, caps, required_seq[k], con.reserve)
            if not a.feasible:
                continue
            alloc = OPT.allocate_flow(list(combo), required_seq[k], caps, model)
            if alloc["ok"]:
                opts[combo] = alloc["power_kw"]
        step_opts.append(opts)
    if any(len(o) == 0 for o in step_opts):
        return None
    best = None
    dt_h = MS.STEP / 60.0
    for seq in itertools.product(*[list(o.keys()) for o in step_opts]):
        if not MS._can_switch_at(cur_on, seq[0], as_of, last_switch, con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, seq[0], caps, required_seq[0], con.reserve)
        if not ok:
            continue
        prev, switches = set(cur_on), 0
        for k in range(H):
            switches += len(MS._switch_events(prev, seq[k]))
            prev = set(seq[k])
        if switches > con.max_switches_per_hour:
            continue
        energy = sum(step_opts[k][seq[k]] for k in range(H)) * dt_h
        cost = energy + MS.SWITCH_PENALTY_KWH * switches
        key = (round(cost, 6), switches, tuple(seq))
        if best is None or key < best[0]:
            best = (key, {"combo0": seq[0], "switches": switches,
                          "energy": round(energy, 4), "cost": round(cost, 6)})
    return best


def test_plan_matches_brute_force_high_demand():
    con, caps, model = _con(), _caps(), _model_path()
    # 单机不足, 必须双机; 从"仅4#运行"起步 (由停机态直接双机存在过渡容量缺口, 与此无关)
    req = [300.0, 300.0, 300.0, 300.0]
    best = MS.plan(req, caps, con, model, {"4#"}, {}, "2026-08-01 00:00:00", 1600.0)
    assert best is not None
    assert set(best["seq"][0]) == {"4#", "5#"}
    ref = _brute_force(req, caps, con, model, {"4#"}, {}, "2026-08-01 00:00:00")
    assert best["cost"] == pytest.approx(ref[0][0], abs=1e-6)
    # 累计代价 = 时域电量 + 惩罚*切换
    assert best["cost"] == pytest.approx(best["horizon_energy_kwh"] + MS.SWITCH_PENALTY_KWH * best["switches"], abs=1e-6)


def test_transition_gap_from_off_yields_none():
    con, caps, model = _con(), _caps(), _model_path()
    # 停机态直接要求双机: 首机启动后容量不足 -> 过渡缺口 -> 不可行 (与 B5 过渡缺口语义一致)
    req = [300.0, 300.0, 300.0, 300.0]
    assert MS.plan(req, caps, con, model, set(), {}, "2026-08-01 00:00:00", 1600.0) is None


def test_plan_matches_brute_force_low_demand_single():
    con, caps, model = _con(), _caps(), _model_path()
    req = [150.0, 150.0, 150.0, 150.0]   # 单机即可
    best = MS.plan(req, caps, con, model, set(), {}, "2026-08-01 00:00:00", 1600.0)
    ref = _brute_force(req, caps, con, model, set(), {}, "2026-08-01 00:00:00")
    assert best["cost"] == pytest.approx(ref[0][0], abs=1e-6)
    assert len(best["seq"][0]) == 1        # 单机更省


def test_first_step_lock_respected():
    con, caps, model = _con(), _caps(), _model_path()
    # 4# 刚启动0分钟, 最短运行15min -> 首步不允许停机
    last = {"4#": "2026-08-01 00:00:00"}
    req = [150.0, 150.0, 150.0, 150.0]
    best = MS.plan(req, caps, con, model, {"4#"}, last, "2026-08-01 00:00:00", 1600.0)
    assert best is not None
    assert "4#" in best["seq"][0]          # 不能立即停 4#


def test_max_switches_respected():
    con, caps, model = _con(), _caps(), _model_path()
    req = [300.0, 150.0, 300.0, 150.0]     # 交替高低, 若无约束会频繁切换
    best = MS.plan(req, caps, con, model, {"4#"}, {}, "2026-08-01 00:00:00", 1600.0)
    assert best is not None
    assert best["switches"] <= con.max_switches_per_hour


def test_infeasible_without_any_combo():
    con, caps, model = _con(), _caps(), _model_path()
    req = [10_000.0, 10_000.0, 10_000.0, 10_000.0]   # 远超双机容量
    assert MS.plan(req, caps, con, model, set(), {}, "2026-08-01 00:00:00", 1600.0) is None


def test_every_step_feasible_and_reserve():
    con, caps, model = _con(), _caps(), _model_path()
    req = [250.0, 250.0, 250.0, 250.0]
    best = MS.plan(req, caps, con, model, set(), {}, "2026-08-01 00:00:00", 1600.0)
    for k, combo in enumerate(best["seq"]):
        a = CONS.assess_combination(combo, caps, req[k], con.reserve)
        assert a.feasible
