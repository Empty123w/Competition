"""压力动态仿真 v2 — 修正单位一致性 / 共同随机数 / 可复现 / 反馈因果与正确供气引用。

保留原始 `pressure_sim_dynamics.py` 与其结果(83.8%)不变; 本脚本为新版本, 输出新文件。

确认并修正的问题 (P0):
  P1-A 单位混用: 原式 `p += surplus/K_VOL - leak*(p-P_ENV)` 未乘 dt, 把"每分钟"物理量与
       "每采样步(dt=15min)"经验差分混在一起。本版**统一为每分钟物理积分**:
       p_{k+1} = p_k + dt*[ (sup_k - dem_k)/K_VOL - LEAK*(p_k - P_ENV) ] + noise_k
       单位: sup/dem [m3/min], K_VOL [m3/MPa], LEAK [1/min], dt=STEP [min], noise [MPa]。
       (为使量级与原版可比, K_VOL 按 dt 折算: 原 per-step 1580 -> per-min 23700=1580*15)
  P1-B 随机性: 原用 `hash(name)` 选种子 -> 跨进程不可复现, 且两策略噪声不同(不公平)。
       本版**固定整数种子 + 共同随机数(CRN)**: 两策略用**同一噪声数组**。
  P1-C 反馈候选错误引用: 原 `_metrics(pf, plans["multi_step"], demand)` 用了旧供气序列。
       本版指标用**反馈自身供气**。
  P1-D 反馈非因果风险: 原先整体仿真再改供气。本版改为**在线因果**闭环:
       每一步仅用"当前已知压力"决定供气(不使用未来)。

输出:
  analysis/reports/b/pressure_sim_dynamics_v2.json
  analysis/reports/b/pressure_sim_dynamics_v2_ledger.csv
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("analysis/reports/b/pressure_sim_dynamics_v2.json")
LED = Path("analysis/reports/b/pressure_sim_dynamics_v2_ledger.csv")
BAND = (0.55, 0.66)
STEP = 15                     # min
DT = float(STEP)              # min
P_ENV = 0.55
P0 = 0.60
NOISE = 0.003
P_MIN, P_MAX = 0.40, 0.75
P_SET = 0.60
KC = 500.0                    # 控制器增益 [m3/min per MPa]
SUP_CAP = 2 * 268.9
RATED = 268.9
# K_VOL [m3/MPa] 为**每分钟**储气常数; 原 per-step 1580 折算为 23700=1580*15
K_VOL = 23700.0
LEAK_PER_MIN = 0.001
# 敏感性: 与原 per-step 网格 {800,1580,3000} 对应的每分钟值
SENS_GRID = {"K_VOL": [12000.0, 23700.0, 45000.0], "LEAK_PER_MIN": [0.0, 0.001, 0.003],
             "NOISE": [0.0, 0.003, 0.006]}
SEED_BASE = 12345             # 固定种子(跨进程可复现)


def _lag_analysis():
    s = pd.read_parquet("data/processed/station_timeseries.parquet",
                        columns=["time", "header_pressure_mpa", "total_flow_m3_min"])
    s["time"] = pd.to_datetime(s["time"])
    s = s[(s.header_pressure_mpa > 0) & s.total_flow_m3_min.notna()].reset_index(drop=True)
    s["dp"] = s.header_pressure_mpa.diff(); s["dq"] = s.total_flow_m3_min.diff()
    d = s.dropna(); dp = d["dp"].to_numpy(); dq = d["dq"].to_numpy()
    lag = {}
    for k in range(6):
        lag[f"k{k}"] = round(float(np.corrcoef(dp, dq)[0, 1] if k == 0 else np.corrcoef(dp[k:], dq[:-k])[0, 1]), 4)
    return {"lag_corr_dp_dq": lag,
            "large_dq_mean_absdp": round(float(d[d["dq"].abs() > 30]["dp"].abs().mean()), 5),
            "overall_mean_absdp": round(float(d["dp"].abs().mean()), 5),
            "pressure_lag1_autocorr": round(float(np.corrcoef(
                s.header_pressure_mpa.to_numpy()[1:], s.header_pressure_mpa.to_numpy()[:-1])[0, 1]), 4)}


def _simulate(supply, demand, k_vol, leak, noise_arr, closed_loop=True, pressure_feedback=False):
    """每分钟物理积分; noise_arr 由外部给定(CRN); closed_loop 用上一时刻压力(因果)。

    返回 (p, sup_eff): sup_eff 为实际供气(含控制器/反馈)。
    """
    n = len(supply)
    p = np.empty(n); sup_eff = np.empty(n)
    cur = P0
    for i in range(n):
        sup = supply[i]
        if closed_loop:
            sup = sup + KC * (P_SET - cur)          # 只用"当前"(i-1时刻)已知压力, 因果
        if pressure_feedback and cur < 0.58:
            sup = min(demand[i] + 40.0, SUP_CAP)    # 因果反馈: 仅依据已知压力
        sup = min(max(sup, 0.0), SUP_CAP)
        # 每分钟物理积分 (dt 显式)
        cur = cur + DT * ((sup - demand[i]) / k_vol - leak * (cur - P_ENV)) \
            + (noise_arr[i] if noise_arr is not None else 0.0)
        cur = min(max(cur, P_MIN), P_MAX)
        p[i] = cur; sup_eff[i] = sup
    return p, sup_eff


def _noise(n, sigma, seed):
    if not sigma:
        return np.zeros(n)
    return np.random.default_rng(seed).normal(0.0, sigma, n)


def _metrics(p, sup_eff, demand):
    inband = (p >= BAND[0]) & (p <= BAND[1])
    return {"compliance_rate": round(float(inband.mean()), 4),
            "min_pressure_mpa": round(float(p.min()), 4), "max_pressure_mpa": round(float(p.max()), 4),
            "low_pressure_steps": int((p < BAND[0]).sum()),
            "low_pressure_minutes": int((p < BAND[0]).sum()) * STEP,
            "fluctuation_std_mpa": round(float(np.std(np.diff(p))), 5),
            "peak_to_peak_mpa": round(float(p.max() - p.min()), 4),
            "shortfall_m3": round(float(np.clip(demand - sup_eff, 0, None).sum()) * STEP, 1),
            "supply_m3": round(float(sup_eff.sum()) * STEP, 1)}


def main():
    led = pd.read_csv("analysis/reports/b/multistep_optimization_ledger.csv")
    demand = led["obs_flow"].to_numpy(float)
    demand = np.where(np.isfinite(demand), demand, 0.0)
    raw = {"b1_rule": led["b1_rule_flow"].to_numpy(float),
           "multi_step": led["multi_step_flow"].to_numpy(float)}
    for k in raw:
        raw[k] = np.where(np.isfinite(raw[k]), raw[k], demand)

    rep = {"protocol": {
        "window": "2026-08-01~08-20 (valid, 重复查看窗口)", "step_minutes": STEP,
        "integration": "per-minute physical: p+=dt*((sup-dem)/K_VOL - LEAK*(p-P_ENV)) + noise",
        "units": {"supply/demand": "m3/min", "K_VOL": "m3/MPa", "LEAK": "1/min", "dt": "min", "noise": "MPa"},
        "band_mpa": list(BAND), "closed_loop": True, "randomness": "CRN: 两策略同一噪声数组",
        "seed_base": SEED_BASE, "uses_B5_final_window": False,
        "evidence_scope": "独立动态仿真; 非现场; 不改冻结 B2/B5",
        "note_K_VOL": "K_VOL=23700=原 per-step 1580 x dt(15), 使量级与原版可比 (纯单位一致化)"}}
    rep["lag_analysis"] = _lag_analysis()

    # 基准: 共同随机数
    base = {}
    for name, sup in raw.items():
        noise = _noise(len(sup), NOISE, SEED_BASE + 1)   # 两策略同一数组
        p, se = _simulate(sup, demand, K_VOL, LEAK_PER_MIN, noise, closed_loop=True)
        base[name] = _metrics(p, se, demand)
    rep["base_case"] = base
    rep["base_case"]["delta_vs_b1"] = {
        "compliance_rate_diff": round(base["multi_step"]["compliance_rate"] - base["b1_rule"]["compliance_rate"], 4),
        "fluctuation_std_diff": round(base["multi_step"]["fluctuation_std_mpa"] - base["b1_rule"]["fluctuation_std_mpa"], 5)}

    # 参数敏感性 (CRN; 每格同一噪声)
    sens = {}
    for kv in SENS_GRID["K_VOL"]:
        for lk in SENS_GRID["LEAK_PER_MIN"]:
            for ns in SENS_GRID["NOISE"]:
                noise = _noise(len(demand), ns, SEED_BASE + 1)
                r = {}
                for name, sup in raw.items():
                    p, _ = _simulate(sup, demand, kv, lk, noise, closed_loop=True)
                    r[name] = {"compliance_rate": round(float(((p >= BAND[0]) & (p <= BAND[1])).mean()), 4),
                               "min_pressure_mpa": round(float(p.min()), 4)}
                r["multi_step_compliance_ge_b1"] = bool(r["multi_step"]["compliance_rate"] >= r["b1_rule"]["compliance_rate"] - 0.001)
                sens[f"K{kv}_L{lk}_N{ns}"] = r
    rep["sensitivity"] = sens
    rep["sensitivity_robust_sign"] = {
        "multi_step_compliance_ge_b1_share": round(float(np.mean([v["multi_step_compliance_ge_b1"] for v in sens.values()])), 3)}

    # 压力反馈候选 (在线因果; 指标用反馈自身供气)
    noise_fb = _noise(len(demand), NOISE, SEED_BASE + 1)
    pf, sup_fb = _simulate(raw["multi_step"], demand, K_VOL, LEAK_PER_MIN, noise_fb,
                           closed_loop=True, pressure_feedback=True)
    rep["pressure_feedback_candidate"] = {
        "definition": "在线因果候选(仅仿真): 当前压力<0.58 时供气=demand+40(封顶双机额定)",
        "metrics": _metrics(pf, sup_fb, demand),
        "note": "压力下限仍 0.55, 不放松; 仅仿真, 非现场; 指标使用反馈自身供气(修正原引用错误)"}

    pd.DataFrame({"demand": demand, "b1_supply": raw["b1_rule"], "opt_supply": raw["multi_step"]}).to_csv(LED, index=False)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep["base_case"], ensure_ascii=False, indent=2))
    print("robust:", rep["sensitivity_robust_sign"])
    print("feedback:", json.dumps(rep["pressure_feedback_candidate"]["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
