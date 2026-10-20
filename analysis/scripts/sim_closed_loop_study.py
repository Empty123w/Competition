"""闭环仿真多目标验证 + 敏感性 (工程假设; 非现场达标)。

- 场景: normal/low/peak/step/fault; 策略: b1/b3/balanced/load_balanced;
- 指标: 压力合格率/波动/供气/能耗/SEC/低负荷/回退/放空;
- 敏感性: K_VOL / LEAK / 合格带 压力带宽 (只做基准改变, 不改场景)。
输出: analysis/reports/b/simulation/{sim_closed_loop.json, sim_ledger.csv, sim_sensitivity.json}
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.simulation.closed_loop import SCENARIOS, SimParams, simulate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/reports/b/simulation"
STRATS = {"b1": "b1", "b3": "b3", "bal": "balanced", "lrb_t10": "load_balanced"}
SCEN_NAMES = ["normal", "low", "peak", "step", "fault"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rep = {"note": "工程假设下模拟验证; 非真实 SCADA 达标; 用气需求为独立模拟量",
           "params_baseline": asdict(SimParams(seed=0)), "scenarios": {}, "sensitivity": {}}
    rows = []
    for scn in SCEN_NAMES:
        rep["scenarios"][scn] = {}
        for label, mode in STRATS.items():
            r = simulate(mode, SCENARIOS[scn], SimParams(seed=0))
            rep["scenarios"][scn][label] = r["metrics"]
            for rec in r["records"]:
                rows.append({"scenario": scn, "strategy": label, **rec})
        b1 = rep["scenarios"][scn]["b1"]
        for label in STRATS:
            m = rep["scenarios"][scn][label]
            m["pressure_std_reduction_vs_b1_pct"] = round((b1["pressure_std"] - m["pressure_std"]) / b1["pressure_std"] * 100, 3) if b1["pressure_std"] else None
            m["sec_reduction_vs_b1_pct"] = round((b1["sec_kwh_per_m3"] - m["sec_kwh_per_m3"]) / b1["sec_kwh_per_m3"] * 100, 3) if b1["sec_kwh_per_m3"] and m["sec_kwh_per_m3"] else None
            m["low_reduction_vs_b1_pct"] = round((b1["low_load_min"] - m["low_load_min"]) / b1["low_load_min"] * 100, 3) if b1["low_load_min"] else None
    pd.DataFrame(rows).to_csv(OUT / "sim_ledger.csv", index=False)

    # 敏感性 (normal, b3)
    sens = {}
    for kv in (12000.0, 23700.0, 45000.0):
        for lk in (0.0, 0.001, 0.003):
            r = simulate("b3", SCENARIOS["normal"], SimParams(seed=0, k_vol_m3_per_mpa=kv, leak_per_min=lk))
            sens[f"kvol={int(kv)},leak={lk}"] = {k: r["metrics"][k] for k in
                ("pressure_compliance", "pressure_std", "pressure_mean", "energy_kwh", "supply_m3", "blowoff_m3")}
    rep["sensitivity"]["storage_leak_normal_b3"] = sens
    # 合格带敏感性 (仅影响统计, 不改场景/控制)
    band_sens = {}
    for lo, hi in ((0.55, 0.66), (0.54, 0.67), (0.50, 0.70)):
        p = SimParams(seed=0, band_lo_mpa=lo, band_hi_mpa=hi)
        r = simulate("b3", SCENARIOS["normal"], p)
        band_sens[f"band=[{lo},{hi}]"] = {"pressure_compliance": r["metrics"]["pressure_compliance"],
                                          "pressure_mean": r["metrics"]["pressure_mean"],
                                          "pressure_std": r["metrics"]["pressure_std"]}
    rep["sensitivity"]["band_statistics_only"] = band_sens

    (OUT / "sim_closed_loop.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "sim_sensitivity.json").write_text(json.dumps(rep["sensitivity"], ensure_ascii=False, indent=2), encoding="utf-8")
    for scn in SCEN_NAMES:
        print("==", scn)
        for label in STRATS:
            m = rep["scenarios"][scn][label]
            print("   %-8s comp %.3f pstd %.4f sec %.4f low %6.0f gap %7.0f blow %7.0f fall %d" % (
                label, m["pressure_compliance"], m["pressure_std"], m["sec_kwh_per_m3"] or 0,
                m["low_load_min"], m["supply_gap_m3"], m["blowoff_m3"], m["fallback_steps"]))


if __name__ == "__main__":
    main()
