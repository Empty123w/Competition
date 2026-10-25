"""第三阶段: 比赛指标统一验收 (同一联合闭环, 相同约束, 冻结评价)。

- 策略: b1 / b3 / balanced / load_balanced / mpc / joint
- 测试种子: 3,4 (与 dev 0,1 / valid 2 独立); 结果取多种子均值
- 同一运行统计: 能效(SEC)、供气满足、加载率D3、低负荷、压力合格率/波动、切换、回退/降级、决策时延
- 可行工况 (normal/low/step/peak) 与容量受限工况 (fault) 分别报告; 不拼接、不冒充全场景达标
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.simulation import closed_loop as CL
from air_station.stage2 import joint_dispatch as JD

OUT = Path("analysis/reports/b/stage3")
STRATEGIES = ("b1", "b3", "balanced", "load_balanced", "mpc", "joint")
FEASIBLE = ("normal", "low", "step", "peak")
TEST_SEEDS = (3, 4)
TARGETS = {"energy_pct": 6.0, "low_pct": 60.0, "compliance": 0.995,
           "std_reduction_pct": 30.0, "d3": 1.0 - 0.10, "latency_s": 60.0}


def _w(st):
    if st == "mpc":
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"meta": {"validation_mode": "simulation", "test_seeds": list(TEST_SEEDS),
                       "targets": TARGETS, "band": [CL.SimParams().band_lo_mpa, CL.SimParams().band_hi_mpa]},
              "scenarios": {}, "pooled": {}}
    per_strat = {st: {"E": 0.0, "V": 0.0, "low": 0.0, "d3": [], "comp": [], "std": []} for st in STRATEGIES}
    base = {k: {"E": 0.0, "V": 0.0, "low": 0.0, "std": []} for k in STRATEGIES}
    for sc_name, sc in CL.SCENARIOS.items():
        block = {}
        for st in STRATEGIES:
            ms = [JD.simulate(st, sc, sim=CL.SimParams(seed=s), weights=_w(st))["metrics"]
                  for s in TEST_SEEDS]
            blk = _avg(ms)
            base_blk = None
            block[st] = blk
            per_strat[st]["E"] += blk["energy_kwh"] * len(TEST_SEEDS)
            per_strat[st]["V"] += blk["supply_m3"] * len(TEST_SEEDS)
            per_strat[st]["low"] += blk["low_load_min"] * len(TEST_SEEDS)
            per_strat[st]["d3"].append(blk["load_ratio_d3_meet"])
            per_strat[st]["comp"].append(blk["pressure_compliance"])
            per_strat[st]["std"].append(blk["pressure_std"])
        # vs b1
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

    for st in STRATEGIES:
        s = per_strat[st]
        b1 = per_strat["b1"]
        pooled_sec = s["E"] / s["V"]; b1_sec = b1["E"] / b1["V"]
        report["pooled"][st] = {
            "sec_kwh_per_m3": round(pooled_sec, 6),
            "sec_reduction_vs_b1_pct": round((b1_sec - pooled_sec) / b1_sec * 100, 2),
            "low_reduction_vs_b1_pct": (round((b1["low"] - s["low"]) / b1["low"] * 100, 2)
                                        if b1["low"] else None),
            "d3_mean": round(float(np.mean(s["d3"])), 4),
            "compliance_feasible_min": round(float(min(s["comp"][i] for i, n in enumerate(CL.SCENARIOS)
                                                       if n in FEASIBLE)), 4),
            "compliance_fault": round(float(s["comp"][list(CL.SCENARIOS).index("fault")]), 4),
            "std_reduction_vs_b1_pct_mean_feasible": round(float(np.mean(
                [(b1["std"][i] - s["std"][i]) / b1["std"][i] * 100
                 for i, n in enumerate(CL.SCENARIOS) if n in FEASIBLE])), 2),
        }

    (OUT / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(report, OUT / "ACCEPTANCE.md")
    print("[stage3-acceptance] done")
    for st in STRATEGIES:
        p = report["pooled"][st]
        print(f"  {st:14s} sec_dn={p['sec_reduction_vs_b1_pct']}% low_dn={p['low_reduction_vs_b1_pct']}% "
              f"D3={p['d3_mean']} comp_feas_min={p['compliance_feasible_min']} comp_fault={p['compliance_fault']} "
              f"std_dn={p['std_reduction_vs_b1_pct_mean_feasible']}%")


def _md(report, path):
    L = ["# 比赛指标统一验收 (第三阶段; 同一联合闭环; validation_mode=simulation)", "",
         f"- 测试种子: {report['meta']['test_seeds']} (dev 0,1 / valid 2 独立)",
         f"- 压力合格带: {report['meta']['band']} MPa (CAL-05 工程示例)", ""]
    L += ["## 池化 KPI (可行工况 normal/low/step/peak)", "",
          "| 策略 | SEC降% | 低负荷降% | D3均值 | 压力合格率(可行最小) | 波动降%(可行均值) | fault合格率 |",
          "|---|---|---|---|---|---|---|"]
    for st in STRATEGIES:
        p = report["pooled"][st]
        L.append(f"| {st} | {p['sec_reduction_vs_b1_pct']} | {p['low_reduction_vs_b1_pct']} | "
                 f"{p['d3_mean']} | {p['compliance_feasible_min']} | {p['std_reduction_vs_b1_pct_mean_feasible']} | "
                 f"{p['compliance_fault']} |")
    L += ["", "## joint 分场景", "",
          "| 场景 | 合格率 | 波动std | SEC降% | 低负荷降% | D3 | 需求满足度 | 切换 | 回退/降级 | p95时延ms |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for sc_name, b in report["scenarios"].items():
        m = b["joint"]
        L.append(f"| {sc_name} | {m['pressure_compliance']} | {m['pressure_std']} | "
                 f"{m['sec_reduction_vs_b1_pct']} | {m['low_reduction_vs_b1_pct']} | {m['load_ratio_d3_meet']} | "
                 f"{m['demand_satisfaction']} | {m['switch_events']} | {m['fallback_steps']} | {m['decide_ms_p95']} |")
    L += ["", "## 逐项目标判定 (以 joint 为准)", "",
          "| 指标 | 目标 | joint 结果 | 判定 |", "|---|---|---|---|"]
    p = report["pooled"]["joint"]; t = report["meta"]["targets"]
    L.append(f"| 综合能效 | ≥{t['energy_pct']}% | {p['sec_reduction_vs_b1_pct']}% | "
             f"{'达标' if p['sec_reduction_vs_b1_pct']>=t['energy_pct'] else '未达'} |")
    L.append(f"| 加载率偏离度 | ≤±10% (D3≥0.90) | D3均值 {p['d3_mean']} | "
             f"{'达标' if p['d3_mean']>=0.90 else '未达'} |")
    L.append(f"| 大马拉小车 | ≥{t['low_pct']}% | {p['low_reduction_vs_b1_pct']}% | "
             f"{'达标' if (p['low_reduction_vs_b1_pct'] or 0)>=t['low_pct'] else '未达'} |")
    L.append(f"| 压力合格率(可行) | ≥{t['compliance']} | {p['compliance_feasible_min']} | "
             f"{'达标' if p['compliance_feasible_min']>=t['compliance'] else '未达'} |")
    L.append(f"| 压力波动下降 | ≥{t['std_reduction_pct']}% | {p['std_reduction_vs_b1_pct_mean_feasible']}% | "
             f"{'达标' if p['std_reduction_vs_b1_pct_mean_feasible']>=t['std_reduction_pct'] else '未达'} |")
    L.append(f"| 压力合格率(fault) | ≥{t['compliance']} | {p['compliance_fault']} | 容量受限, 客观未达 |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
