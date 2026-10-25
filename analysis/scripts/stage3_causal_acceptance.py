"""第三阶段: 因果预测下的联合指标复核。

- 用 `forecast_mode="causal"` 重新评估 joint (无未来泄漏), 并与 `ideal`(原第三阶段) 对照;
- 保留原结果 (analysis/reports/b/stage3/acceptance.json) 不覆盖;
- 统计: 能效/SEC、加载率D3、低负荷、压力合格率/波动、累计供气、需求满足度、未满足缺口、切换、回退/降级、时延;
- 同时报告可行工况 (normal/low/step/peak) 与全部工况 (含 fault 容量不足);
- 附因果预测精度 (逐时域 MAE) 说明。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.simulation import closed_loop as CL
from air_station.stage2 import joint_dispatch as JD
from air_station.stage2.causal_forecast import CausalForecaster

OUT = Path("analysis/reports/b/stage3")
STRATEGIES = ("b1", "b3", "balanced", "load_balanced", "mpc", "joint")
FEASIBLE = ("normal", "low", "step", "peak")
TEST_SEEDS = (3, 4)


def _w(st):
    if st in ("mpc",):
        return JD.JointWeights(w_energy=1.0, w_low=0.0, w_imbalance=0.0,
                               w_pressure=5000.0, w_center=20000.0, w_switch=2.0, w_short=800.0)
    if st == "joint":
        return JD.JointWeights(w_energy=1.0, w_low=1.5, w_imbalance=40.0, w_pressure=5000.0,
                               w_center=20000.0, w_switch=2.0, w_short=800.0,
                               balance_mode="spread", spread_tol=0.10)
    return None


def _avg(met):
    keys = [k for k in met[0] if isinstance(met[0][k], (int, float)) and met[0][k] is not None]
    return {k: round(float(np.mean([m[k] for m in met])), 5) for k in keys}


def forecast_mae(method="persistence", seeds=TEST_SEEDS, H=4):
    """因果预测在多步时域上的 MAE (m3/min), 对照真值。"""
    out = {}
    for name, sc in CL.SCENARIOS.items():
        errs = [[] for _ in range(H)]
        for sd in seeds:
            rng = np.random.default_rng(sd)
            dem = CL.demand_series(sc)
            fc = CausalForecaster(method=method, period=288)
            p = CL.SimParams(seed=sd)
            for k in range(sc.n_steps):
                obs = dem[k] + rng.normal(0, p.forecast_noise_frac * max(50.0, dem[k]))
                fc.observe(obs)
                fut = fc.predict_future(H)
                for j in range(H):
                    kk = min(sc.n_steps - 1, k + 1 + j)
                    errs[j].append(abs(fut[j] - dem[kk]))
        out[name] = [round(float(np.mean(e)), 2) for e in errs]
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"meta": {"validation_mode": "simulation", "test_seeds": list(TEST_SEEDS),
                       "forecast_mode": "causal", "forecast_method": "persistence",
                       "note": "ideal 为原第三阶段(含未来真值前瞻)对照, 保留不覆盖"},
              "forecast_mae_m3min": forecast_mae("persistence"),
              "scenarios": {}, "pooled": {}}

    per = {st: {"E": 0.0, "V": 0.0, "low": 0.0, "d3": [], "comp": [], "std": [],
                "supply": 0.0, "demand": 0.0, "sat": [], "unmet": 0.0} for st in STRATEGIES}
    joint_ideal = {"E": 0.0, "V": 0.0, "low": 0.0, "d3": [], "comp": [], "std": [],
                   "supply": 0.0, "demand": 0.0, "sat": [], "unmet": 0.0}

    for sc_name, sc in CL.SCENARIOS.items():
        block = {}
        for st in STRATEGIES:
            ms = [JD.simulate(st, sc, sim=CL.SimParams(seed=s), weights=_w(st),
                              forecast_mode="causal")["metrics"] for s in TEST_SEEDS]
            blk = _avg(ms); block[st] = blk
            per[st]["E"] += blk["energy_kwh"] * len(TEST_SEEDS)
            per[st]["V"] += blk["supply_m3"] * len(TEST_SEEDS)
            per[st]["low"] += blk["low_load_min"] * len(TEST_SEEDS)
            per[st]["d3"].append(blk["load_ratio_d3_meet"]); per[st]["comp"].append(blk["pressure_compliance"])
            per[st]["std"].append(blk["pressure_std"])
        # joint under ideal (对照)
        mi = [JD.simulate("joint", sc, sim=CL.SimParams(seed=s), weights=_w("joint"),
                          forecast_mode="ideal")["metrics"] for s in TEST_SEEDS]
        bi = _avg(mi); block["joint_ideal"] = bi
        joint_ideal["E"] += bi["energy_kwh"] * len(TEST_SEEDS)
        joint_ideal["V"] += bi["supply_m3"] * len(TEST_SEEDS)
        joint_ideal["low"] += bi["low_load_min"] * len(TEST_SEEDS)
        joint_ideal["d3"].append(bi["load_ratio_d3_meet"]); joint_ideal["comp"].append(bi["pressure_compliance"])
        joint_ideal["std"].append(bi["pressure_std"])
        for st in STRATEGIES:
            b1 = block["b1"]; m = block[st]
            m["sec_reduction_vs_b1_pct"] = round((b1["sec_kwh_per_m3"] - m["sec_kwh_per_m3"])
                                                 / b1["sec_kwh_per_m3"] * 100, 2)
            m["low_reduction_vs_b1_pct"] = (round((b1["low_load_min"] - m["low_load_min"])
                                                  / b1["low_load_min"] * 100, 2)
                                            if b1["low_load_min"] else None)
            m["std_reduction_vs_b1_pct"] = (round((b1["pressure_std"] - m["pressure_std"])
                                                  / b1["pressure_std"] * 100, 2)
                                            if b1["pressure_std"] else None)
        report["scenarios"][sc_name] = block

    for tag, agg in (("joint", per["joint"]), ("joint_ideal", joint_ideal)):
        sec = agg["E"] / agg["V"]; b1 = per["b1"]; b1sec = b1["E"] / b1["V"]
        report["pooled"][tag] = {
            "sec_kwh_per_m3": round(sec, 6),
            "sec_reduction_vs_b1_pct": round((b1sec - sec) / b1sec * 100, 2),
            "low_reduction_vs_b1_pct": (round((b1["low"] - agg["low"]) / b1["low"] * 100, 2)
                                        if b1["low"] else None),
            "d3_mean": round(float(np.mean(agg["d3"])), 4),
            "compliance_feasible_min": round(float(min(agg["comp"][i] for i, n in enumerate(CL.SCENARIOS)
                                                       if n in FEASIBLE)), 4),
            "compliance_fault": round(float(agg["comp"][list(CL.SCENARIOS).index("fault")]), 4),
            "std_reduction_vs_b1_pct_mean_feasible": round(float(np.mean(
                [(b1["std"][i] - agg["std"][i]) / b1["std"][i] * 100
                 for i, n in enumerate(CL.SCENARIOS) if n in FEASIBLE])), 2),
        }
    for st in STRATEGIES:
        p = per[st]; b1 = per["b1"]
        report["pooled"][st] = {
            "sec_kwh_per_m3": round(p["E"] / p["V"], 6),
            "sec_reduction_vs_b1_pct": round((b1["E"] / b1["V"] - p["E"] / p["V"]) / (b1["E"] / b1["V"]) * 100, 2),
            "low_reduction_vs_b1_pct": (round((b1["low"] - p["low"]) / b1["low"] * 100, 2) if b1["low"] else None),
            "d3_mean": round(float(np.mean(p["d3"])), 4),
            "compliance_feasible_min": round(float(min(p["comp"][i] for i, n in enumerate(CL.SCENARIOS)
                                                       if n in FEASIBLE)), 4),
            "compliance_fault": round(float(p["comp"][list(CL.SCENARIOS).index("fault")]), 4),
            "std_reduction_vs_b1_pct_mean_feasible": round(float(np.mean(
                [(b1["std"][i] - p["std"][i]) / b1["std"][i] * 100
                 for i, n in enumerate(CL.SCENARIOS) if n in FEASIBLE])), 2),
        }

    (OUT / "causal_acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(report, OUT / "CAUSAL_ACCEPTANCE.md")
    print("[stage3-causal] done")
    for tag in ("joint_ideal", "joint"):
        p = report["pooled"][tag]
        print(f"  {tag:12s} sec_dn={p['sec_reduction_vs_b1_pct']}% low_dn={p['low_reduction_vs_b1_pct']}% "
              f"D3={p['d3_mean']} comp_feas_min={p['compliance_feasible_min']} comp_fault={p['compliance_fault']} "
              f"std_dn={p['std_reduction_vs_b1_pct_mean_feasible']}%")
    print("  baseline b1 sec=0; others:", {st: report["pooled"][st]["sec_reduction_vs_b1_pct"] for st in STRATEGIES})


def _md(report, path):
    L = ["# 因果预测下的联合指标复核 (第三阶段; validation_mode=simulation)", "",
         f"- 预测模式: **causal** (无未来泄漏), 方法=persistence; 对照=**ideal**(原第三阶段, 含未来真值前瞻)",
         f"- 测试种子: {report['meta']['test_seeds']}", "",
         "## 因果预测精度 (MAE, m3/min; 真值对照)", "",
         "| 场景 | +1步 | +2步 | +3步 | +4步 |", "|---|---|---|---|---|"]
    for sc, v in report["forecast_mae_m3min"].items():
        L.append(f"| {sc} | {v[0]} | {v[1]} | {v[2]} | {v[3]} |")
    L += ["", "## 池化 KPI (可行工况 normal/low/step/peak)", "",
          "| 策略 | SEC降% | 低负荷降% | D3均值 | 压力合格率(可行min) | 波动降%(可行均值) | fault合格率 |",
          "|---|---|---|---|---|---|---|"]
    for tag in ("joint_ideal", "joint", "mpc", "load_balanced", "balanced", "b3", "b1"):
        p = report["pooled"][tag]
        L.append(f"| {tag} | {p['sec_reduction_vs_b1_pct']} | {p['low_reduction_vs_b1_pct']} | {p['d3_mean']} | "
                 f"{p['compliance_feasible_min']} | {p['std_reduction_vs_b1_pct_mean_feasible']} | {p['compliance_fault']} |")
    L += ["", "## joint (causal) 分场景详细", "",
          "| 场景 | 合格率 | 波动std | SEC降% | 低负荷降% | D3 | 累计供气m³ | 需求m³ | 满足度 | 未满足缺口m³ | 切换 | 回退/降级 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sc_name, b in report["scenarios"].items():
        m = b["joint"]
        L.append(f"| {sc_name} | {m['pressure_compliance']} | {m['pressure_std']} | {m['sec_reduction_vs_b1_pct']} | "
                 f"{m['low_reduction_vs_b1_pct']} | {m['load_ratio_d3_meet']} | {m['supply_m3']} | {m['demand_m3']} | "
                 f"{m['demand_satisfaction']} | {m['unserved_deficit_m3']} | {m['switch_events']} | {m['fallback_steps']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
