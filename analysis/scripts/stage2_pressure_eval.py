"""第二阶段: 压力 MPC 评估运行器。

对照: reactive_pi (基线) / mpc (解析压力模型) / mpc_ml (ML 压力模型)。
场景: normal / low / step / peak / fault (沿用 closed_loop 的工程场景)。
输出: 压力合格率、波动(std)、能耗、需求满足度、未满足缺口; 并给出波动下降%。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from air_station.simulation import closed_loop as CL
from air_station.stage2 import pressure_model as PM
from air_station.stage2 import pressure_sim as PS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="analysis/reports/b/stage2/pressure")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    pm_lgb = PM.train("lightgbm")
    pm_lin = PM.train("linear")
    report = {"meta": {"validation_mode": "simulation",
                       "pressure_model": {"lightgbm": pm_lgb.metrics, "linear": pm_lin.metrics}},
              "scenarios": {}}

    for name, sc in CL.SCENARIOS.items():
        block = {}
        for strat in ("reactive_pi", "mpc", "mpc_ml"):
            res = PS.simulate(strat, sc, pmodel=(pm_lgb if strat == "mpc_ml" else None))
            block[strat] = res["metrics"]
        base = block["reactive_pi"]
        for strat in ("mpc", "mpc_ml"):
            m = block[strat]
            denom = base["pressure_std"] or 1e-9
            m["pressure_std_reduction_pct"] = round((base["pressure_std"] - m["pressure_std"]) / denom * 100, 2)
            m["pressure_compliance_gain_pp"] = round((m["pressure_compliance"] - base["pressure_compliance"]) * 100, 2)
        report["scenarios"][name] = block

    (out / "pressure_mpc_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    _write_md(report, out / "PRESSURE_MPC.md")
    print("[stage2-pressure] done")
    for name, b in report["scenarios"].items():
        print(f"  {name:7s} comp(pi/mpc/ml)="
              f"{b['reactive_pi']['pressure_compliance']:.3f}/{b['mpc']['pressure_compliance']:.3f}/{b['mpc_ml']['pressure_compliance']:.3f}"
              f"  std={b['reactive_pi']['pressure_std']:.4f}->{b['mpc']['pressure_std']:.4f}"
              f"  E={b['reactive_pi']['energy_kwh']:.0f}->{b['mpc']['energy_kwh']:.0f}")


def _write_md(report, path):
    L = ["# 压力 MPC 评估 (第二阶段; validation_mode=simulation)", "",
         f"- ML 压力模型: lightgbm test={report['meta']['pressure_model']['lightgbm'].get('test')}",
         f"- 线性压力模型: {report['meta']['pressure_model']['linear'].get('test')}", "",
         "| 场景 | 策略 | 合格率 | 波动std | 波动降% | 能耗kWh | 需求满足度 | 未满足缺口m³ |",
         "|---|---|---|---|---|---|---|---|"]
    for name, b in report["scenarios"].items():
        for strat in ("reactive_pi", "mpc", "mpc_ml"):
            m = b[strat]
            red = m.get("pressure_std_reduction_pct", "-")
            L.append(f"| {name} | {strat} | {m['pressure_compliance']} | {m['pressure_std']} | {red} | "
                     f"{m['energy_kwh']} | {m['demand_satisfaction']} | {m['unserved_deficit_m3']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
