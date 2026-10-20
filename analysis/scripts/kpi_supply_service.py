"""供气服务 KPI: 供气量 / 历史参考缺口 / 不可行步 / 安全回退 (只读账本)。

- 供气量: 各策略交付 m3; 与历史参考(obs)比较。
- 缺口: 每步 max(0, obs - flow)*15。
- 不可行步: status!=success 计数; 安全回退: success & !exec。
- 严格不把"少供气"计入节能: 另行报告 dV 与 SEC 口径。
输出: analysis/reports/b/kpi_supply_service.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEP, RATED = 15, 268.9
STRAT = ["b1_rule", "b3_b4", "bal"]


def main():
    rep = {"note": "供气与节电分列; dV<0 表示较 B1 少供气, 不得当作能效提升"}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        out = {"n_steps": int(len(df)), "obs_m3": round(float(df["obs"].fillna(0).sum()) * STEP, 1)}
        mm = pd.Series(True, index=df.index)
        for s in STRAT:
            mm &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        out["paired_exec_steps"] = int(mm.sum())
        out["paired_exec_obs_m3"] = round(float(df.loc[mm, "obs"].fillna(0).sum()) * STEP, 1)
        for s in STRAT:
            flow = df[f"{s}_flow"].fillna(0.0)
            gap = (df["obs"].fillna(0.0) - flow).clip(lower=0) * STEP
            over = (flow - df["obs"].fillna(0.0)).clip(lower=0) * STEP
            infeas = int(df[f"{s}_status"].ne("success").sum())
            succ = df[f"{s}_status"].eq("success")
            blocked = int((succ & ~df[f"{s}_exec"].fillna(False)).sum())
            out[s] = {
                "supply_m3": round(float(flow.sum()) * STEP, 1),
                "supply_vs_obs_m3": round(float(flow.sum()) * STEP - float(df["obs"].fillna(0).sum()) * STEP, 1),
                "gap_m3": round(float(gap.sum()), 1),
                "over_supply_m3": round(float(over.sum()), 1),
                "infeasible_steps": infeas,
                "safety_blocked_steps": blocked,
                "executed_steps": int((succ & df[f"{s}_exec"].fillna(False)).sum()),
                "paired_gap_m3": round(float((df.loc[mm, "obs"].fillna(0.0) - df.loc[mm, f"{s}_flow"].fillna(0.0)).clip(lower=0).sum()) * STEP, 1),
            }
        # 历史参考缺口: obs 与 B1 实际运行历史(此处以 b1_rule 作为规则基线轨迹)
        rep[w] = out
    OUT = ROOT / "analysis/reports/b/kpi_supply_service.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
