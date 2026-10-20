"""加载率偏离度 KPI (逐机 + 全站), 从开发/验证账本计算。

口径: λ = flow/268.9; 适用样本 = 运行且 flow∈[120,268.9] (排除停机/越界/无效)。
目标: A: λ*=1.0 (额定); B: 逐机效率平台下界 (4#=0.8537, 5#=0.9381)。
偏离 = (λ-λ*)/λ* ×100 (相对%); 超限 = |偏离|>10%。CAL-04 未确认, 同时报 A/B。
输出: analysis/reports/b/kpi_load_ratio.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RATED = 268.9
LO, HI = 120.0, 268.9
TARGET_A = {"4#": 1.0, "5#": 1.0}
TARGET_B = {"4#": 0.8537, "5#": 0.9381}
TOL = 10.0
STRAT = ["b1_rule", "b3_b4", "bal"]


def device_stats(f: pd.Series, target: float):
    q = f[(f > 0) & (f >= LO - 1e-6) & (f <= HI + 1e-6)]
    n = int(len(q))
    if n == 0:
        return {"n": 0}
    lam = q / RATED
    dev = (lam - target) / target * 100.0
    return {"n": n, "lambda_mean": round(float(lam.mean()), 4),
            "dev_mean_pct": round(float(dev.mean()), 3),
            "dev_absmax_pct": round(float(dev.abs().max()), 3),
            "exceed_ratio": round(float((dev.abs() > TOL).mean()), 4),
            "exceed_minutes": float((dev.abs() > TOL).sum() * 15)}


def main():
    rep = {"definition": {
        "load_ratio": "flow/268.9", "applicable": "运行且 flow∈[120,268.9]",
        "tolerance_pct": TOL,
        "target_A_rated": TARGET_A, "target_B_plateau_lo": TARGET_B,
        "cal04_status": "pending", "note": "A/B 两目标都报, 不据验证结果改目标"}}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        m = pd.Series(True, index=df.index)
        for s in STRAT:
            m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        g = df.loc[m]
        out = {}
        for s in STRAT:
            dev = {}
            for d in ("4#", "5#"):
                f = g[f"{s}_f{d[0]}"]
                dev[d] = {"A_rated": device_stats(f, TARGET_A[d]),
                          "B_plateau_lo": device_stats(f, TARGET_B[d])}
            # 全站: 运行机数 n -> lambda_station = total/(n*RATED)
            tot = g[f"{s}_flow"].fillna(0.0)
            on = ((g[f"{s}_f4"] > 0).astype(int) + (g[f"{s}_f5"] > 0).astype(int))
            ok = (on > 0) & (tot >= LO - 1e-6)
            lam_st = (tot[ok] / (on[ok] * RATED))
            dev["station"] = {"n": int(ok.sum()),
                              "lambda_mean": round(float(lam_st.mean()), 4),
                              "dev_mean_vs1_pct": round(float((lam_st - 1.0).mean() * 100), 3),
                              "dev_absmax_vs1_pct": round(float((lam_st - 1.0).abs().max() * 100), 3),
                              "exceed_ratio_vs1": round(float(((lam_st - 1.0).abs() > TOL / 100).mean()), 4)}
            out[s] = dev
        rep[w] = out
    OUT = ROOT / "analysis/reports/b/kpi_load_ratio.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
