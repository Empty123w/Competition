"""任务一: 评估正确性修复 (不覆盖旧结果, 另存)。

修复点:
  1) 区分 执行 / 安全回退(blocked或过渡缺口, 有方案未执行) / 不可行(无合法方案);
  2) "节电率"仅在 **双方均可执行** 与 **服务等价(双方均满足实测需求)** 子集上计算;
     未执行步的零电耗**不计为有效节能**;
  3) 压力钳制诊断 (0.75 上界) 与净供需;
  4) 历史字段盘点 (压力动态可用性)。
输出: analysis/reports/b/eval_corrected.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LED = ROOT / "analysis/reports/b/ard_experiment_v2_ledger.csv"
OUT = ROOT / "analysis/reports/b/eval_corrected.json"
STRAT = ("b1_rule", "b3_b4", "cand", "ard_err", "ard_dyn", "ard_dyn_peak")
STEP = 15
P0 = 0.60
K_VOL, LEAK, P_ENV = 23700.0, 0.001, 0.55


def _cls(df, s):
    st = df[f"{s}_status"].astype(str)
    ex = df[f"{s}_exec"].fillna(False).astype(bool)
    ok = st == "success"
    return np.where(ok & ex, "executed", np.where(ok & ~ex, "blocked_not_exec", "infeasible"))


def _pressure(df, s, idx):
    g = df.loc[idx]
    dem = g["obs"].ffill().fillna(0).clip(lower=0).to_numpy()
    sup = g[f"{s}_flow"].fillna(g["obs"]).fillna(0).to_numpy()
    p = P0; ps = []
    for i in range(len(dem)):
        p = p + STEP * ((sup[i] - dem[i]) / K_VOL - LEAK * (p - P_ENV))
        p = min(max(p, 0.40), 0.75)
        ps.append(p)
    ps = np.array(ps)
    return {"compliance": round(float(((ps >= 0.55) & (ps <= 0.66)).mean()), 4),
            "pinned_upper_frac": round(float((ps >= 0.7499).mean()), 4),
            "pinned_lower_frac": round(float((ps <= 0.4001).mean()), 4),
            "p_end": round(float(ps[-1]), 4), "net_supply_minus_demand_m3": round(float((sup - dem).sum()) * STEP, 1)}


def _save(df, idx, dt_h):
    out = {}
    b1e = float(df.loc[idx, "b1_rule_power"].fillna(0).sum()) * dt_h
    for s in STRAT:
        g = df.loc[idx]
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        out[s] = {"energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                  "gap_vs_actual_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 2),
                  "sec": round(E / V, 6) if V else None,
                  "saving_vs_b1_pct": round((b1e - E) / b1e * 100, 4) if b1e else None}
    return out


def main():
    df = pd.read_csv(LED)
    dt_h = STEP / 60.0
    rep = {"source": str(LED.name), "fixes": {
        "no_zero_energy_as_saving": "未执行步不计为节能; 节电率仅在配对可执行/服务等价子集计算",
        "classes": ["executed", "blocked_not_exec", "infeasible"]}, "windows": {}}

    for w in df["window"].unique():
        g = df[df["window"] == w].reset_index(drop=True)
        wrep = {"n": int(len(g)), "class_counts": {}}
        for s in STRAT:
            c = pd.Series(_cls(g, s)).value_counts().to_dict()
            wrep["class_counts"][s] = {k: int(c.get(k, 0)) for k in ("executed", "blocked_not_exec", "infeasible")}
        # 配对可执行 (b1 & s) 且 双方均成功
        for s in STRAT:
            if s == "b1_rule":
                continue
            pair = (g["b1_rule_status"].astype(str) == "success") & g["b1_rule_exec"].fillna(False) \
                & (g[f"{s}_status"].astype(str) == "success") & g[f"{s}_exec"].fillna(False)
            wrep.setdefault("paired_exec", {})[s] = int(pair.sum())
        # 服务等价 (配对可执行 且 双方供气>=实测需求)
        for s in STRAT:
            if s == "b1_rule":
                continue
            svc = (g["b1_rule_status"].astype(str) == "success") & g["b1_rule_exec"].fillna(False) \
                & (g[f"{s}_status"].astype(str) == "success") & g[f"{s}_exec"].fillna(False) \
                & (g["b1_rule_flow"].fillna(0) >= g["obs"] - 0.05) & (g[f"{s}_flow"].fillna(0) >= g["obs"] - 0.05)
            wrep.setdefault("service_equiv", {})[s] = int(svc.sum())
        # 全窗口(保留不可行, 但明确非节能口径)
        wrep["full_window_energy_only"] = _save(g, g.index, dt_h)
        # 配对可执行子集 saving (相对 b1)
        pe = (g["b1_rule_status"].astype(str) == "success") & g["b1_rule_exec"].fillna(False)
        for s in STRAT:
            if s != "b1_rule":
                pe = pe & (g[f"{s}_status"].astype(str) == "success") & g[f"{s}_exec"].fillna(False)
        wrep["paired_exec_steps"] = int(pe.sum())
        wrep["paired_exec_saving"] = _save(g, g.index[pe], dt_h) if pe.any() else {}
        # 服务等价子集 saving
        svc = pe.copy()
        for s in STRAT:
            svc = svc & (g[f"{s}_flow"].fillna(0) >= g["obs"] - 0.05)
        wrep["service_equiv_steps"] = int(svc.sum())
        wrep["service_equiv_saving"] = _save(g, g.index[svc], dt_h) if svc.any() else {}
        # 压力/钳制 (配对可执行子集)
        if pe.any():
            wrep["pressure"] = {s: _pressure(g, s, g.index[pe]) for s in STRAT}
        rep["windows"][w] = wrep

    # 历史字段盘点
    rep["pressure_dynamics_field_inventory"] = {
        "station_timeseries": ["header_pressure_mpa", "total_flow_m3_min", "total_power_kw",
                               "station_temperature_c", "station_humidity_percent", "load_rate", "load_level"],
        "device_timeseries": ["discharge_pressure_mpa", "flow_m3_min", "power_kw", "igv_percent",
                              "bov_closed_percent", "current_proxy_a", "vibration_stage_*_mm_s", "runtime_hours", "load_hours"],
        "usable": "header_pressure(状态), total_flow(吞吐), 逐机状态/IGV/BOV/排气压力 -> 可做短时可辨识",
        "missing_assumptions": ["独立耗气(demand)测点", "管网储气容积/时延", "阀门响应时间"],
        "marked": "K_VOL/LEAK/延迟 均为 simulation 假设, 无厂家依据"}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:6000])


if __name__ == "__main__":
    main()
