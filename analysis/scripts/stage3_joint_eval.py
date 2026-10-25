"""第三阶段: 统一闭环联合调度评估运行器。

同一物理/场景/约束下比较: b1, b3, balanced, load_balanced, mpc, joint。
输出统一 KPI 表 (能效/SEC、低负荷、加载率D3、压力合格率/波动、需求满足度、切换、降级/回退、决策时延)。
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


def _joint_weights():
    return JD.JointWeights(w_energy=1.0, w_low=1.5, w_imbalance=40.0,
                           w_pressure=5000.0, w_center=20000.0, w_switch=2.0, w_short=800.0)


def _mpc_weights():
    return JD.JointWeights(w_energy=1.0, w_low=0.0, w_imbalance=0.0,
                           w_pressure=5000.0, w_center=20000.0, w_switch=2.0, w_short=800.0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"meta": {"validation_mode": "simulation", "dt_min": CL.SimParams().dt_min,
                       "band": [CL.SimParams().band_lo_mpa, CL.SimParams().band_hi_mpa],
                       "strategies": list(STRATEGIES), "feasible_scenarios": list(FEASIBLE)},
              "scenarios": {}}
    for sc_name, sc in CL.SCENARIOS.items():
        block = {}
        for st in STRATEGIES:
            w = _mpc_weights() if st == "mpc" else (_joint_weights() if st == "joint" else None)
            r = JD.simulate(st, sc, weights=w)
            block[st] = r["metrics"]
        base = block["b1"]
        for st in STRATEGIES:
            m = block[st]
            m["sec_reduction_vs_b1_pct"] = round((base["sec_kwh_per_m3"] - m["sec_kwh_per_m3"])
                                                 / base["sec_kwh_per_m3"] * 100, 2)
            m["low_reduction_vs_b1_pct"] = round((base["low_load_min"] - m["low_load_min"])
                                                 / base["low_load_min"] * 100, 2) if base["low_load_min"] else None
            m["std_reduction_vs_b1_pct"] = round((base["pressure_std"] - m["pressure_std"])
                                                 / base["pressure_std"] * 100, 2) if base["pressure_std"] else None
        report["scenarios"][sc_name] = block

    (OUT / "joint_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(report, OUT / "JOINT_EVAL.md")
    print("[stage3-joint] done")
    for sc_name, b in report["scenarios"].items():
        print(f"  {sc_name}")
        for st in STRATEGIES:
            m = b[st]
            print(f"    {st:14s} comp={m['pressure_compliance']} std={m['pressure_std']} "
                  f"SEC_dn={m['sec_reduction_vs_b1_pct']}% low_dn={m['low_reduction_vs_b1_pct']}% "
                  f"D3={m['load_ratio_d3_meet']} sat={m['demand_satisfaction']} fb={m['fallback_steps']} "
                  f"decide_p95={m['decide_ms_p95']}ms")


def _md(report, path):
    L = ["# 联合闭环调度统一评估 (第三阶段; validation_mode=simulation)", "",
         "同一物理/场景/约束; 能效/供气/加载率/压力/安全同一运行统计。", "",
         "| 场景 | 策略 | 压力合格率 | 波动std | SEC降% | 低负荷降% | D3达标 | 需求满足度 | 切换 | 回退/降级 | p95时延ms |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sc_name, b in report["scenarios"].items():
        for st in report["meta"]["strategies"]:
            m = b[st]
            L.append(f"| {sc_name} | {st} | {m['pressure_compliance']} | {m['pressure_std']} | "
                     f"{m['sec_reduction_vs_b1_pct']} | {m['low_reduction_vs_b1_pct']} | {m['load_ratio_d3_meet']} | "
                     f"{m['demand_satisfaction']} | {m['switch_events']} | {m['fallback_steps']} | {m['decide_ms_p95']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
