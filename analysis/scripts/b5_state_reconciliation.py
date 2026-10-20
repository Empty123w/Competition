"""B5 最终状态对账: 4735 / 4712 / 4710 / medium 交集与最终比较窗口 (只读)。

输出: analysis/reports/b/b5_state_reconciliation.json
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

LED = Path("analysis/reports/b/b5_ledger.csv")
OUT = Path("analysis/reports/b/b5_state_reconciliation.json")
STEP = 5


def E(g, c):
    return float(g[c].dropna().sum()) * STEP / 60.0


def V(g, c):
    return float(g[c].dropna().sum()) * STEP


def main():
    df = pd.read_csv(LED)
    r = df.b3_reason.fillna("")
    S_common = (df.b1_status == "success") & (df.b3_status == "success")
    S_b4 = S_common & (~df.b3_blocked)
    S_exec = S_common & df.b3_exec
    minstop = df.index[r.str.contains("MIN_STOP_LOCK")]
    gap = df.index[r.str.contains("TRANSITION_CAPACITY_GAP")]
    medium = df.index[df.b3_surge.eq("medium")]

    rep = {}
    rep["sets"] = {
        "common_capacity_feasible": int(S_common.sum()),
        "b4_risk_retained": int(S_b4.sum()),
        "final_executable": int(S_exec.sum()),
    }
    rep["rejection_timestamps"] = {
        "min_stop_lock": df.loc[minstop, "t"].tolist(),
        "transition_gap": df.loc[gap, "t"].tolist(),
        "same_steps": sorted(set(df.loc[minstop, "t"]) & set(df.loc[gap, "t"])),
        "note": "最短停机与过渡缺口若为同一步, 拒绝原因不得重复相加",
    }
    rep["medium"] = {
        "n_total": int(len(medium)),
        "blocked": int(df.loc[medium, "b3_blocked"].sum()),
        "executed": int(df.loc[medium, "b3_exec"].sum()),
        "in_common": int(S_common.loc[medium].sum()),
        "in_b4_retained": int(S_b4.loc[medium].sum()),
        "in_final_executable": int(S_exec.loc[medium].sum()),
    }
    # 最终有效比较窗口 = 最终可执行 4710
    fin = df[S_exec]
    rep["final_window"] = {
        "n": int(len(fin)),
        "b1_energy_kwh": round(E(fin, "b1_power"), 2),
        "b3_energy_kwh": round(E(fin, "b3_power"), 2),
        "b1_supply_m3": round(V(fin, "b1_flow"), 1),
        "b3_supply_m3": round(V(fin, "b3_flow"), 1),
        "b1_shortfall_m3": round(float((fin["required"] - fin["b1_flow"]).clip(lower=0).sum()) * STEP, 3),
        "b3_shortfall_m3": round(float((fin["required"] - fin["b3_flow"]).clip(lower=0).sum()) * STEP, 3),
        "b1_sec": round(E(fin, "b1_power") / V(fin, "b1_flow"), 6),
        "b3_sec": round(E(fin, "b3_power") / V(fin, "b3_flow"), 6),
        "energy_reduction_pct": round((E(fin, "b1_power") - E(fin, "b3_power")) / E(fin, "b1_power") * 100, 4),
        "sec_reduction_pct": round((E(fin, "b1_power") / V(fin, "b1_flow")
                                    - E(fin, "b3_power") / V(fin, "b3_flow"))
                                   / (E(fin, "b1_power") / V(fin, "b1_flow")) * 100, 4),
        "same_timesteps": True,
        "field_confirmable": "unknown (offline simulation)",
        "note": "B1 与 B3 使用完全相同的 4710 个时间步",
    }
    # B4 风险保留 4712 (含 2 个过渡缺口步)
    ret = df[S_b4]
    rep["b4_retained_window"] = {
        "n": int(len(ret)),
        "energy_reduction_pct": round((E(ret, "b1_power") - E(ret, "b3_power")) / E(ret, "b1_power") * 100, 4),
        "sec_reduction_pct": round((E(ret, "b1_power") / V(ret, "b1_flow")
                                    - E(ret, "b3_power") / V(ret, "b3_flow"))
                                   / (E(ret, "b1_power") / V(ret, "b1_flow")) * 100, 4),
        "note": "含 2 个过渡缺口步, 其 B3 计划不可执行; 仅作对照",
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
