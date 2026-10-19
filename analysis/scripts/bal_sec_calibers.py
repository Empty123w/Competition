"""核验 bal / B3 的 SEC 指标口径 (从原始账本独立重算, 不复用 kpis())。

三口径:
  full_window   : 全窗; b1 与策略各自仅在自身有供气的步上计入 (口径不同步, 不可直接比)
  paired_exec   : 配对可执行; 所有策略 status==success 且 exec==True 的步
  service_equiv : 服务等价; 在 paired_exec 基础上, 各策略 flow >= obs-0.05
对每种口径分别报: 样本数, b1/策略 电量E、供气V、SEC, 以及 SEC 降幅、同供气节电率。
输出: analysis/reports/b/bal_sec_calibers.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEP, RATED = 15, 268.9
DT_H = STEP / 60.0
STRAT = ["b1_rule", "b3_b4", "bal"]


def _low_min(g, s):
    return float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                  + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)


def _agg(g, s):
    E = float(g[f"{s}_power"].fillna(0).sum()) * DT_H
    V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
    return E, V, (E / V if V else None)


def caliber(df, name, idx):
    g = df.loc[idx]
    b1E, b1V, b1sec = _agg(g, "b1_rule")
    b1low = _low_min(g, "b1_rule")
    out = {"caliber": name, "steps": int(len(g)),
           "b1": {"E_kwh": round(b1E, 1), "V_m3": round(b1V, 1), "SEC": round(b1sec, 6) if b1sec else None,
                  "low_min": b1low}}
    for s in ("b3_b4", "bal"):
        E, V, sec = _agg(g, s)
        out[s] = {"E_kwh": round(E, 1), "V_m3": round(V, 1), "SEC": round(sec, 6) if sec else None,
                  "low_min": _low_min(g, s),
                  "SEC_reduction_pct": round((b1sec - sec) / b1sec * 100, 4) if b1sec and sec else None,
                  "energy_saving_same_supply_pct": round((b1E - E) / b1E * 100, 4) if b1E else None,
                  "dSEC_vs_b3_pct_point": None}
    out["bal"]["dSEC_vs_b3_pct_point"] = round(
        (out["b3_b4"]["SEC_reduction_pct"] - out["bal"]["SEC_reduction_pct"]), 4)
    return out


def main():
    rep = {"note": "独立重算; 三口径分别报样本数与数值; full_window 各策略自有供气步不同步, 仅作参考"}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        rep[w] = {}
        rep[w]["full_window"] = caliber(df, "full_window", df.index)
        m = pd.Series(True, index=df.index)
        for s in STRAT:
            m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        rep[w]["paired_exec"] = caliber(df, "paired_exec", df.index[m])
        m2 = m.copy()
        for s in STRAT:
            m2 &= df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05
        rep[w]["service_equiv"] = caliber(df, "service_equiv", df.index[m2])
    OUT = ROOT / "analysis/reports/b/bal_sec_calibers.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
