"""B3 优化核心测试: 分配、目标分项、RUL/健康惩罚。"""
from __future__ import annotations

from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization.contracts import RulAssessment
from tests.optimization import _b3_helpers as H


def _opt_cfg():
    from air_station.optimization.service import load_optimization_config
    return load_optimization_config()["dispatch"]


def test_allocation_sums_to_demand(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    alloc = OPT.allocate_flow(("4#", "5#"), 300.0, caps, power_model_path)
    assert alloc["ok"] and alloc["demand_met"]
    assert abs(sum(alloc["flows"].values()) - 300.0) < 1e-6
    assert abs(alloc["total_flow_m3min"] - 300.0) < 1e-6


def test_greedy_prefers_lower_marginal(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    alloc = OPT.allocate_flow(("4#", "5#"), 300.0, caps, power_model_path)
    # 5# 边际功耗更低(依据 B2 模型), 应优先加载
    assert alloc["flows"]["5#"] >= alloc["flows"]["4#"]
    assert alloc["flows"]["4#"] >= caps["4#"].min_flow_m3min


def test_single_machine_allocation(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    alloc = OPT.allocate_flow(("4#",), 200.0, caps, power_model_path)
    assert abs(alloc["flows"]["4#"] - 200.0) < 1e-6


def test_objective_terms_normalized_and_reported(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    caps = CONS.build_capabilities(con, H.snapshot(running=(True, True)), [], None)
    r = OPT.evaluate_combo(("4#", "5#"), caps, 300.0, con.reserve, ("4#", "5#"),
                           H.snapshot(running=(True, True)), [], cfg, power_model_path, {})
    assert r["feasible"]
    t = r["terms"]
    assert set(t) == {"power", "switch", "health", "rul"}
    # 归一化: power 项应显著小于 1, 且 total == 加权和
    assert 0 < t["power"] < 1
    w = cfg["weights"]
    expect = (w["power"] * t["power"] + w["switch"] * t["switch"]
              + w["health"] * t["health"] + w["rul"] * t["rul"])
    assert abs(r["total_objective"] - expect) < 1e-4


def test_switch_penalty_on_start(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    caps = CONS.build_capabilities(con, H.snapshot(running=(True, False)), [], None)
    r = OPT.evaluate_combo(("4#", "5#"), caps, 300.0, con.reserve, ("4#",),
                           H.snapshot(running=(True, False)), [], cfg, power_model_path, {})
    if r["feasible"]:
        assert r["n_switches"] == 1 and r["terms"]["switch"] > 0


def test_rul_penalty_toggle(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    cfg = dict(cfg)
    cfg["rul"] = {"enabled": True, "simulation": True, "threshold_hours": 500.0}
    rul_map = {"4#": RulAssessment("4#", 100.0), "5#": RulAssessment("5#", 900.0)}
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    r = OPT.evaluate_combo(("4#",), caps, 200.0, con.reserve, ("4#",),
                           H.snapshot(), [], cfg, power_model_path, rul_map)
    assert r["terms"]["rul"] > 0 and r["rul_devices_used"] == 1
    # 关闭时 rul 项为 0
    cfg2 = dict(cfg)
    cfg2["rul"] = {"enabled": False, "simulation": True, "threshold_hours": 500.0}
    r2 = OPT.evaluate_combo(("4#",), caps, 200.0, con.reserve, ("4#",),
                            H.snapshot(), [], cfg2, power_model_path, rul_map)
    assert r2["terms"]["rul"] == 0.0


def test_health_penalty_term(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    r = OPT.evaluate_combo(("4#",), caps, 200.0, con.reserve, ("4#",),
                           H.snapshot(), [H.health("4#", "预警")], cfg, power_model_path, {})
    assert r["terms"]["health"] > 0


def test_sum_energy_kwh_units():
    # 1 分钟步长: 100 kW 持续 60 步 = 100 kWh
    assert abs(OPT.sum_energy_kwh([100.0] * 60, 1.0) - 100.0) < 1e-9
    # 直接把 kW 相加不是 kWh
    assert abs(OPT.sum_energy_kwh([100.0] * 60, 1.0) - 6000.0) > 1.0
    # 15 分钟步长, 200 kW x 4 步 = 200 kWh
    assert abs(OPT.sum_energy_kwh([200.0] * 4, 15.0) - 200.0) < 1e-9


def test_power_cap_marks_infeasible(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    cfg = dict(cfg)
    cfg["power_cap_kw_per_device"] = 50.0        # 远低于任何估计功率
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    r = OPT.evaluate_combo(("4#",), caps, 200.0, con.reserve, ("4#",),
                           H.snapshot(), [], cfg, power_model_path, {})
    assert not r["feasible"] and C.RC_POWER_OVER_CAP in r["reason_codes"]


def test_greedy_matches_grid_optimum(power_model_path):
    import numpy as np
    con = H.constraints(reserve={"mode": "none"})
    caps = CONS.build_capabilities(con, H.snapshot(), [], None)
    lo0, hi0 = caps["4#"].min_flow_m3min, caps["4#"].max_flow_m3min
    lo1, hi1 = caps["5#"].min_flow_m3min, caps["5#"].max_flow_m3min
    for req in (300.0, 400.0, 500.0, 520.0):   # 需求需 >= 两机最小流量之和(240)
        g = OPT.allocate_flow(("4#", "5#"), req, caps, power_model_path)
        best = None
        for q0 in np.linspace(lo0, hi0, 600):
            q1 = req - q0
            if q1 < lo1 - 1e-9 or q1 > hi1 + 1e-9:
                continue
            p = (PERF.estimate_power("4#", q0, model_path=power_model_path)["estimated_power_kw"]
                 + PERF.estimate_power("5#", q1, model_path=power_model_path)["estimated_power_kw"])
            best = p if best is None else min(best, p)
        assert best is not None
        # 贪心为线性功率模型下的最优, 不劣于离散网格
        assert g["power_kw"] <= best + 1e-6


def test_search_returns_four_results(power_model_path):
    con = H.constraints(reserve={"mode": "none"})
    cfg = _opt_cfg()
    s = OPT.search_combinations(H.snapshot(), 300.0, con, [], None, cfg, power_model_path)
    assert len(s["results"]) == 4
    assert s["best"] is not None
