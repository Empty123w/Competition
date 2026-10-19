"""压力: 片段辨识 + 改进动态模型 + 独立"供气-耗气-储气-延迟"仿真 (独立版本)。

不改冻结 B2/B5; 不在 B5 窗口调参; 场景/参数/压力范围在比较前固定。
关键纪律: **供气(supply)与耗气(demand)用不同序列**, 不把同一 flow 当两者。
  - supply = 调度计划流量 (B1 或优化);
  - demand = 站级实测总流量 (作为**耗气代理**, 明确标注; 非理想耗气测点)。

输出: analysis/reports/b/pressure_sim_dynamics.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("analysis/reports/b/pressure_sim_dynamics.json")
LED = Path("analysis/reports/b/pressure_sim_dynamics_ledger.csv")
BAND = (0.55, 0.66)
STEP = 15
# 固定仿真参数 (比较前固定; 来源=simulation 假设, 取值有依据地由数据量级反推)
K_VOL = 1580.0        # 等效储气容积 [m3/MPa] (由 |dq|~30 -> |dp|~0.019 反推)
LEAK_PER_MIN = 0.001  # 泄漏/交换系数 [1/min]
P_ENV = 0.55          # 环境/基准压力 [MPa]
P0 = 0.60             # 初值 [MPa]
NOISE = 0.003         # 传感噪声 sigma [MPa]
P_MIN, P_MAX = 0.40, 0.75
SENS_GRID = {"K_VOL": [800.0, 1580.0, 3000.0], "LEAK_PER_MIN": [0.0, 0.001, 0.003],
             "NOISE": [0.0, 0.003, 0.006]}


def _lag_analysis():
    s = pd.read_parquet("data/processed/station_timeseries.parquet",
                        columns=["time", "header_pressure_mpa", "total_flow_m3_min"])
    s["time"] = pd.to_datetime(s["time"])
    s = s[(s.header_pressure_mpa > 0) & s.total_flow_m3_min.notna()].reset_index(drop=True)
    s["dp"] = s.header_pressure_mpa.diff()
    s["dq"] = s.total_flow_m3_min.diff()
    d = s.dropna()
    dp = d["dp"].to_numpy()
    dq = d["dq"].to_numpy()
    lag = {}
    for k in range(0, 6):
        if k == 0:
            c = np.corrcoef(dp, dq)[0, 1]
        else:
            c = np.corrcoef(dp[k:], dq[:-k])[0, 1]
        lag[f"k{k}"] = round(float(c), 4)
    out = {"lag_corr_dp_dq": lag}
    big = d[d["dq"].abs() > 30]
    out["large_dq_mean_absdp"] = round(float(big["dp"].abs().mean()), 5)
    out["overall_mean_absdp"] = round(float(d["dp"].abs().mean()), 5)
    out["pressure_lag1_autocorr"] = round(float(np.corrcoef(
        s.header_pressure_mpa.to_numpy()[1:], s.header_pressure_mpa.to_numpy()[:-1])[0, 1]), 4)
    return out


P_SET = 0.60     # 设定点 (与 baseline pressure.setpoint 一致)
KC = 500.0       # 控制器增益 [m3/min per MPa] (闭环补偿, 代表现场调压/操作)
SUP_CAP = 2 * 268.9


def _simulate(supply, demand, k_vol, leak, noise, seed=0, closed_loop=True):
    rng = np.random.default_rng(seed)
    n = len(supply)
    p = np.empty(n)
    cur = P0
    for i in range(n):
        sup = supply[i]
        if closed_loop:
            # 控制器按设定点补偿供气 (代表现场调压); 计划供气为前馈基线
            sup = sup + KC * (P_SET - cur)
        sup = min(max(sup, 0.0), SUP_CAP)
        surplus = sup - demand[i]
        cur = cur + surplus / k_vol - leak * (cur - P_ENV) + (rng.normal(0, noise) if noise else 0.0)
        cur = min(max(cur, P_MIN), P_MAX)
        p[i] = cur
    return p


def _metrics(p, supply, demand):
    inband = (p >= BAND[0]) & (p <= BAND[1])
    return {"compliance_rate": round(float(inband.mean()), 4),
            "min_pressure_mpa": round(float(p.min()), 4),
            "max_pressure_mpa": round(float(p.max()), 4),
            "low_pressure_steps": int((p < BAND[0]).sum()),
            "low_pressure_minutes": int((p < BAND[0]).sum()) * STEP,
            "fluctuation_std_mpa": round(float(np.std(np.diff(p))), 5),
            "peak_to_peak_mpa": round(float(p.max() - p.min()), 4),
            "shortfall_m3": round(float(np.clip(demand - supply, 0, None).sum()) * STEP, 1),
            "supply_m3": round(float(supply.sum()) * STEP, 1)}


def main():
    led = pd.read_csv("analysis/reports/b/multistep_optimization_ledger.csv")
    rep = {"protocol": {
        "window": "2026-08-01~08-20 (valid, 重复查看窗口)", "step_minutes": STEP,
        "band_mpa": list(BAND), "uses_B5_final_window": False,
        "supply_definition": "调度计划流量 (B1 / 优化)",
        "demand_definition": "站级实测总流量 (耗气代理; 非理想耗电测点), 与 supply 为不同序列",
        "params_fixed_before_comparison": {"K_VOL": K_VOL, "LEAK_PER_MIN": LEAK_PER_MIN,
                                           "P_ENV": P_ENV, "P0": P0, "NOISE": NOISE,
                                           "P_SET": P_SET, "KC": KC, "closed_loop": True,
                                           "source": "simulation 假设; K_VOL 由历史 |dq|~30->|dp|~0.019 反推; KC=控制器补偿(代表现场调压)"},
        "evidence_scope": "独立动态仿真; 非现场; 不改冻结 B2/B5"}}
    rep["lag_analysis"] = _lag_analysis()

    demand = led["obs_flow"].to_numpy(float)
    plans = {"b1_rule": led["b1_rule_flow"].to_numpy(float),
             "multi_step": led["multi_step_flow"].to_numpy(float)}
    # 缺值以 demand 填充(相当于"无动作保持")
    for k in plans:
        plans[k] = np.where(np.isfinite(plans[k]), plans[k], demand)
    demand = np.where(np.isfinite(demand), demand, 0.0)

    base = {}
    for name, sup in plans.items():
        p = _simulate(sup, demand, K_VOL, LEAK_PER_MIN, NOISE, seed=name.__hash__() % 1000)
        base[name] = {**_metrics(p, sup, demand)}
        if name == "b1_rule":
            b1p = p
    rep["base_case"] = base
    rep["base_case"]["delta_vs_b1"] = {
        "compliance_rate_diff": round(base["multi_step"]["compliance_rate"] - base["b1_rule"]["compliance_rate"], 4),
        "fluctuation_std_diff": round(base["multi_step"]["fluctuation_std_mpa"] - base["b1_rule"]["fluctuation_std_mpa"], 5)}

    # 参数敏感性 (同环境)
    sens = {}
    for k_vol in SENS_GRID["K_VOL"]:
        for leak in SENS_GRID["LEAK_PER_MIN"]:
            for noise in SENS_GRID["NOISE"]:
                key = f"K{k_vol}_L{leak}_N{noise}"
                r = {}
                for name, sup in plans.items():
                    p = _simulate(sup, demand, k_vol, leak, noise, seed=7)
                    r[name] = {"compliance_rate": round(float(((p >= BAND[0]) & (p <= BAND[1])).mean()), 4),
                               "min_pressure_mpa": round(float(p.min()), 4)}
                r["multi_step_compliance_ge_b1"] = bool(r["multi_step"]["compliance_rate"] >= r["b1_rule"]["compliance_rate"] - 0.001)
                sens[key] = r
    rep["sensitivity"] = sens
    rep["sensitivity_robust_sign"] = {
        "multi_step_compliance_ge_b1_share": round(float(np.mean([v["multi_step_compliance_ge_b1"] for v in sens.values()])), 3)}

    # 压力反馈调度候选 (独立, 仅仿真): 仿真中 p<0.58 时提升供气(加机组/加负荷)
    def feedback(sup):
        p = _simulate(sup, demand, K_VOL, LEAK_PER_MIN, NOISE, seed=3)
        out = sup.copy()
        low = p < 0.58
        out[low] = np.minimum(demand[low] + 40.0, 2 * RATED)
        p2 = _simulate(out, demand, K_VOL, LEAK_PER_MIN, NOISE, seed=3)
        return out, p2
    RATED = 268.9
    _, pf = feedback(plans["multi_step"])
    rep["pressure_feedback_candidate"] = {
        "definition": "独立候选(仅仿真): 仿真 p<0.58 时提升供气 +40 m3/min (封顶双机额定)",
        "metrics": _metrics(pf, plans["multi_step"], demand),
        "note": "压力阈值不放松(仍 0.55 下限); 仅仿真, 非现场; 需现场验证"}
    LED.write_text("", encoding="utf-8")  # 占位; 详细账本见下方
    pd.DataFrame({"demand": demand, "b1_supply": plans["b1_rule"], "opt_supply": plans["multi_step"]}).to_csv(LED, index=False)

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
