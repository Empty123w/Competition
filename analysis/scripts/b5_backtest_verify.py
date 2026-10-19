"""B5 连续状态回放验收核查 (只读): 集合一致性/拒绝原因/执行计数/差额分解/压力口径。

输入: b5_per_step.csv(独立回测), b5_ledger.csv(连续回放), b5_backtest_metrics.json
输出: analysis/reports/b/b5_backtest_verification.json
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

IND = Path("analysis/reports/b/b5_per_step.csv")
LED = Path("analysis/reports/b/b5_ledger.csv")
METRICS = Path("analysis/reports/b/b5_backtest_metrics.json")
OUT = Path("analysis/reports/b/b5_backtest_verification.json")
STEP = 5


def E(g, c):
    return float(g[c].dropna().sum()) * STEP / 60.0


def V(g, c):
    return float(g[c].dropna().sum()) * STEP


def main():
    ind = pd.read_csv(IND)
    led = pd.read_csv(LED)
    met = json.loads(METRICS.read_text(encoding="utf-8"))
    rep = {}

    # 1) 集合一致性
    ind_c = set(ind.loc[(ind.b1_status == "success") & (ind.b3_status == "success"), "t"])
    led_c = set(led.loc[(led.b1_status == "success") & (led.b3_status == "success"), "t"])
    rep["common_set_equal"] = {"equal": ind_c == led_c, "indep_n": len(ind_c), "cont_n": len(led_c),
                               "inter": len(ind_c & led_c),
                               "note": "可行集合由需求/备用/容量决定, 与状态无关, 故可评估步仍为 4735"}

    # 2) 可执行规则修正
    succ = led.b3_status == "success"
    gap = succ & led.b3_reason.fillna("").str.contains("TRANSITION_CAPACITY_GAP")
    rep["exec_rule"] = {
        "success_steps": int(succ.sum()),
        "success_with_transition_gap": int(gap.sum()),
        "gap_timestamps": led.loc[gap, "t"].tolist(),
        "blocked_steps": int(led.b3_blocked.sum()),
        "executed_steps": int(led.b3_exec.sum()),
    }

    # 3) 拒绝原因
    def tally(mask_prefix):
        rc = led[mask_prefix].fillna("")
        return {
            "capacity_infeasible": int((led["b1_status"] != "success").sum()) if mask_prefix == "b1_reason" else int((led["b3_status"] == "INFEASIBLE").sum()),
            "insufficient_or_invalid_data": int(rc.str.contains("INSUFFICIENT|INVALID|STALE|FORECAST_MISSING").sum()),
            "min_run_lock": int(rc.str.contains("MIN_RUN_LOCK").sum()),
            "min_stop_lock": int(rc.str.contains("MIN_STOP_LOCK").sum()),
            "max_switch_lock": int(rc.str.contains("MAX_SWITCH_LOCK").sum()),
            "transition_gap": int(rc.str.contains("TRANSITION_CAPACITY_GAP").sum()),
            "surge_blocked": int((led.b3_blocked).sum()) if mask_prefix == "b3_reason" else 0,
            "surge_medium_review": int(rc.str.contains("SURGE_MEDIUM_REVIEW").sum()),
        }
    rep["rejections"] = {"b1": tally("b1_reason"), "b3": tally("b3_reason")}

    # 4) 实际执行的动作数 (运行状态变化)
    def switches(col):
        on = (led[col].fillna(0) > 0).astype(int)
        return int((on.diff().abs() == 1).sum())
    rep["executed_switches"] = {f"{pr}_{d}": switches(f"{pr}_f{d[-2]}") for pr in ("b1", "b3") for d in ("4#", "5#")}
    rep["initial_state"] = {"4#_running": bool(led["b1_f4"].iloc[0] > 0),
                            "5#_running": bool(led["b1_f5"].iloc[0] > 0)}

    # 5) 差额分解 (同一共同可行集合, B4 过滤前)
    m = ind.merge(led, on="t", suffixes=("_ind", "_led"))
    cm = (m.b1_status_ind == "success") & (m.b3_status_ind == "success")
    mm = m[cm]
    d = {}
    for pr in ("b1", "b3"):
        Ei = E(mm, f"{pr}_power_ind"); Ec = E(mm, f"{pr}_power_led")
        Vi = V(mm, f"{pr}_flow_ind"); Vc = V(mm, f"{pr}_flow_led")
        d[pr] = {"indep_energy_kwh": round(Ei, 2), "cont_energy_kwh": round(Ec, 2),
                 "delta_kwh": round(Ec - Ei, 2),
                 "indep_supply_m3": round(Vi, 1), "cont_supply_m3": round(Vc, 1)}
    E1i, E3i = d["b1"]["indep_energy_kwh"], d["b3"]["indep_energy_kwh"]
    E1c, E3c = d["b1"]["cont_energy_kwh"], d["b3"]["cont_energy_kwh"]
    d["reduction_indep_pct"] = round((E1i - E3i) / E1i * 100, 4)
    d["reduction_cont_pct"] = round((E1c - E3c) / E1c * 100, 4)
    d["attribution"] = ("同一步集; 变化来自状态演化: B1 与 B3 的计划随模拟开停/流量反馈改变"
                        "(非窗口/分母/供气任务/筛选变化)")
    rep["decomposition"] = d

    # 6) 压力口径
    hp = led["obs_pressure"]
    station_unknown = led.loc[hp.isna() | (hp <= 0), ["t", "obs_pressure"]]
    rep["pressure"] = {
        "station_header_unknown_steps": int(len(station_unknown)),
        "station_header_unknown_ts": station_unknown["t"].tolist(),
        "device_discharge_missing_causing_b4_unknown": int(led.b3_surge.eq("unknown").sum()),
        "note": "两者口径不同: 前者为站级母管压力缺失; 后者为机组排气压力缺失导致 B4 风险 unknown; 动作后压力反事实未知不计为已验证安全",
    }

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
