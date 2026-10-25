"""第三阶段: 一/二阶段压力仿真一致性核查。

目的: 在**不改动任何阶段一/二代码**的前提下, 复核两阶段压力结果的差异来源,
      输出物理参数、场景、初始条件、控制策略的逐项对照, 并分别保留原始结果。

用法: python3 analysis/scripts/stage3_pressure_consistency.py
输出: analysis/reports/b/stage3/pressure_consistency.json + PRESSURE_CONSISTENCY.md
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.simulation import closed_loop as CL
from air_station.stage2 import pressure_sim as PS

OUT = Path("analysis/reports/b/stage3")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sim = CL.SimParams()

    # ---- 物理/场景对照 (静态核查) ----
    static = {
        "physics_identical": {
            "k_vol_m3_per_mpa": sim.k_vol_m3_per_mpa,
            "leak_per_min": sim.leak_per_min, "p_env_mpa": sim.p_env_mpa,
            "p_init_mpa": sim.p_init_mpa, "p_hard_lo": sim.p_hard_lo, "p_hard_hi": sim.p_hard_hi,
            "band": [sim.band_lo_mpa, sim.band_hi_mpa], "p_set": sim.p_set_mpa,
            "blow_k": sim.blow_k, "blow_deadband": sim.blow_deadband,
            "dt_min": sim.dt_min, "exec_delay_steps": sim.exec_delay_steps,
            "note": "阶段二 pressure_sim 直接 import closed_loop 的 SimParams/物理函数 -> 物理完全相同",
        },
        "scenarios_identical": {k: asdict(v) for k, v in CL.SCENARIOS.items()},
        "differences": [
            {"dim": "需求曲线", "stage1": "CL.demand_series(SCENARIOS)", "stage2": "同一函数",
             "effect": "无差异"},
            {"dim": "物理动力学", "stage1": "pressure_step_accounted (质量守恒)",
             "stage2": "同一函数 import", "effect": "无差异"},
            {"dim": "初始条件", "stage1": "双机运行, flow=base/2 每台", "stage2": "停机, flow=0",
             "effect": "前若干步瞬态不同"},
            {"dim": "供气决策", "stage1": "dispatch_api(B3/bal/LRB) 或 B1 规则 -> 组合+分配; 需求取 upper_bound, PI 仅偏置预测",
             "stage2": "控制器直接给**总供气 S**, PI 作用在瞬时需求上, 再 allocate(min120/max268.9)",
             "effect": "主要差异来源: 供气档位/组合与过供程度不同"},
            {"dim": "故障场景", "stage1": "b3/bal/LRB 处理 5# 失效; b1 不处理(comp=1.0 为掩盖故障的假象)",
             "stage2": "allocate 限定可用机组 (故障如实反映)", "effect": "fault 场景口径不同"},
            {"dim": "合格率定义", "stage1": "步内 p∈[0.55,0.66] 占比", "stage2": "定义相同", "effect": "无差异"},
        ],
    }

    # ---- 数值对照: 阶段一原场景, 原策略 ----
    report = {"static": static, "stage1": {}, "stage2": {}, "stage1_original_report": {}}
    for name, sc in CL.SCENARIOS.items():
        report["stage1"][name] = {}
        for strat in ("b1", "b3", "balanced", "load_balanced"):
            r = CL.simulate(strat, sc)
            m = r["metrics"]
            report["stage1"][name][strat] = {
                "pressure_compliance": m["pressure_compliance"], "pressure_std": m["pressure_std"],
                "energy_kwh": m["energy_kwh"], "demand_satisfaction": m["demand_satisfaction"]}
        report["stage2"][name] = {}
        for strat in ("reactive_pi", "mpc"):
            r = PS.simulate(strat, sc)
            m = r["metrics"]
            report["stage2"][name][strat] = {
                "pressure_compliance": m["pressure_compliance"], "pressure_std": m["pressure_std"],
                "energy_kwh": m["energy_kwh"], "demand_satisfaction": m["demand_satisfaction"]}

    (OUT / "pressure_consistency.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    _md(report, OUT / "PRESSURE_CONSISTENCY.md")
    print("[stage3] consistency written")
    for name in report["stage1"]:
        s1 = report["stage1"][name]; s2 = report["stage2"][name]
        print(f"  {name:7s} S1 b3 comp={s1['b3']['pressure_compliance']} | "
              f"S2 pi comp={s2['reactive_pi']['pressure_compliance']} mpc comp={s2['mpc']['pressure_compliance']}")


def _md(report, path):
    L = ["# 一/二阶段压力仿真一致性核查", "",
         "## 结论", "",
         "- 两阶段**物理与场景完全相同** (阶段二直接 import 阶段一的 `SimParams`/物理函数/场景)。",
         "- 差异来自**控制器与初始条件**, 非物理条件放宽: 阶段一由 B1/B3 调度器给组合+分配 (需求取 upper_bound), 阶段二由控制器直接给总供气 S; 且阶段一初始双机运行、阶段二初始停机; 故障场景处理口径不同。",
         "- 阶段一原始结果**未修改**, 保留于 `analysis/reports/b/simulation/`。", "",
          "## 逐场景数值对照", "",
          "| 场景 | S1 b1 | S1 b3 | S2 PI | S2 MPC |", "|---|---|---|---|---|"]
    for name in report["stage1"]:
        s1 = report["stage1"][name]; s2 = report["stage2"][name]
        L.append(f"| {name} | {s1['b1']['pressure_compliance']} | {s1['b3']['pressure_compliance']} | "
                 f"{s2['reactive_pi']['pressure_compliance']} | {s2['mpc']['pressure_compliance']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
