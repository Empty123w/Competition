"""全窗口(1920 步)账本核算 (复用已有 multistep_optimization_ledger.csv, 不重跑实验)。

产出:
  - 每策略全窗口: 成功/可执行/阻断/不可行/不可评估 计数;
  - 电量/供气/缺供/SEC/供气差异(vs B1);
  - 满足预测(met_forecast) 与满足实测(met_actual) 两口径;
  - 服务等价子集 (各策略均满足实测需求)。
筛选规则与压力证据类型一并记录。
输出: analysis/reports/b/full_window_accounting.json
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

LED = Path("analysis/reports/b/multistep_optimization_ledger.csv")
OUT = Path("analysis/reports/b/full_window_accounting.json")
STEP = 15
PLANNERS = ("b1_rule", "myopic", "multi_step", "oracle")


def _block(g, p, dt_h, step):
    n = len(g)
    E = float(g[f"{p}_power"].sum()) * dt_h if n else 0.0
    V = float(g[f"{p}_flow"].sum()) * step if n else 0.0
    return {"steps": n, "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
            "sec": round(E / V, 6) if V else None,
            "shortfall_vs_observed_m3": round(float((g["obs_flow"] - g[f"{p}_flow"]).clip(lower=0).sum()) * step, 2) if n else 0.0}


def main():
    if not LED.exists():
        raise SystemExit("缺少 multistep_optimization_ledger.csv; 先运行 multistep_optimization.py")
    df = pd.read_csv(LED)
    dt_h = STEP / 60.0
    rep = {"protocol": {
        "window": "2026-08-01~08-20 (valid, 重复查看窗口, 非独立最终测试)",
        "n_steps_total": int(len(df)), "step_minutes": STEP,
        "reserve": "frozen extra 5%", "uses_B5_final_window": False,
        "pressure_evidence_type": "none (无调度反事实压力预测; 合格带 CAL-05 待确认)",
        "filter_rules": {
            "evaluable": "status==success 且 exec=True (未 B4 阻断且过渡供气成立)",
            "unappraisable": "status!=success (infeasible) 或 blocked=True",
            "service_matched": "b1_rule/myopic/multi_step 均 success+exec 且 met_actual=True",
            "met_forecast": "供应量 >= required_forecast (满足预测要求)",
            "met_actual": "供应量 >= obs_flow (满足实测需求)",
            "note": "缺供按 max(obs_flow - supply, 0)*step 计; 不得将少供气低电量当同等服务节能"}}}

    full = {}
    for p in PLANNERS:
        st = df[f"{p}_status"]
        cnt = Counter(st.tolist())
        exec_ok = int(((st == "success") & df[f"{p}_exec"]).sum())
        blocked = int(((st == "success") & df[f"{p}_blocked"]).sum())
        infeas = int((st != "success").sum())
        g = df[(st == "success") & df[f"{p}_exec"]]
        row = {**_block(g, p, dt_h, STEP),
               "success": int((st == "success").sum()), "executable": exec_ok,
               "blocked": blocked, "infeasible": infeas,
               "unappraisable": blocked + infeas,
               "met_forecast_steps": int(df[f"{p}_met_forecast"].sum()),
               "met_actual_steps": int(df[f"{p}_met_actual"].sum()),
               "status_counts": {k: int(v) for k, v in cnt.items()}}
        full[p] = row
    rep["full_window"] = full

    # 供气差异 vs B1 (全窗口可执行)
    b1v = full["b1_rule"]["supply_m3"]
    for p in PLANNERS:
        rep["full_window"][p]["supply_diff_vs_b1_m3"] = round(full[p]["supply_m3"] - b1v, 1)

    # 服务等价子集
    svc = df[[f"{p}_status" for p in ("b1_rule", "myopic", "multi_step")]].eq("success").all(axis=1)
    for p in ("b1_rule", "myopic", "multi_step"):
        svc = svc & df[f"{p}_exec"] & df[f"{p}_met_actual"]
    s = df[svc]
    rep["service_matched"] = {"steps": int(len(s))}
    if len(s):
        for p in ("b1_rule", "myopic", "multi_step"):
            b = _block(s, p, dt_h, STEP)
            b["supply_diff_vs_b1_m3"] = round(b["supply_m3"] - float(s["b1_rule_flow"].sum()) * STEP, 1)
            rep["service_matched"][p] = b
        e1 = rep["service_matched"]["b1_rule"]["energy_kwh"]
        for p in ("myopic", "multi_step"):
            e = rep["service_matched"][p]["energy_kwh"]
            rep["service_matched"][f"{p}_energy_saving_vs_b1_pct"] = round((e1 - e) / e1 * 100, 4)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
