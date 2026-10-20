"""压力-调度工程演示 (仅仿真): 用账本供气/需求序列驱动质量平衡, 比较各策略压力表现。

模型: p_{k+1}=p_k + dt*((sup_k - dem_k)/K_VOL - LEAK*(p_k-P_ENV)), 钳制[0.40,0.75]。
明确: 独立用气需求未测, 反事实不可辨识; 本演示仅说明"过供->上钳制"机制, 不宣称 99.5%。
输出: analysis/reports/b/pressure_dispatch_demo.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BAND = (0.55, 0.66)
K_VOL, LEAK, P_ENV, P0 = 23700.0, 0.001, 0.55, 0.60
CLAMP = (0.40, 0.75)
DT = 15.0
STRAT = ["b1_rule", "b3_b4", "bal", "lrb"]


def simulate(sup, dem, over_supply_extra=40.0):
    """sup: 供气序列; dem: 需求序列; over_supply_extra: 反馈过供量(基线规则特征)。"""
    p = P0
    out = []
    for i in range(len(sup)):
        s = sup[i]
        cur = p + DT * ((s - dem[i]) / K_VOL - LEAK * (p - P_ENV))
        p = float(min(max(cur, CLAMP[0]), CLAMP[1]))
        out.append(p)
    return np.array(out)


def main():
    rep = {"model": {"K_VOL": K_VOL, "LEAK": LEAK, "P_ENV": P_ENV, "P0": P0, "clamp": list(CLAMP),
                     "dt_min": DT, "band": list(BAND), "validation_mode": "simulation",
                     "identifiability": "demand 未测; 反事实不可辨识, 结果仅示意"},
           "over_supply_extra_m3min": 40.0}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        strats = [s for s in STRAT if f"{s}_status" in df.columns]
        m = pd.Series(True, index=df.index)
        for s in strats:
            m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        g = df.loc[m]
        dem = g["obs"].fillna(0).to_numpy(float)
        out = {}
        for s in strats:
            sup = g[f"{s}_flow"].fillna(0).to_numpy(float)
            # 反馈过供: 供气 - 需求, 下限 0
            extra = np.clip(sup - dem, 0, None)
            p = simulate(sup + 0.0, dem)  # 直接用供气序列
            inband = float(((p >= BAND[0]) & (p <= BAND[1])).mean() * 100)
            out[s] = {"in_band_pct": round(inband, 3),
                      "above_band_pct": round(float((p > BAND[1]).mean() * 100), 3),
                      "below_band_pct": round(float((p < BAND[0]).mean() * 100), 3),
                      "mean_over_supply_m3min": round(float(extra.mean()), 3),
                      "mean_p": round(float(p.mean()), 4),
                      "at_upper_clamp_pct": round(float((p >= CLAMP[1] - 1e-6).mean() * 100), 3)}
        # 对照: 无过供 (供气=需求)
        pn = simulate(dem, dem)
        out["no_oversupply"] = {"in_band_pct": round(float(((pn >= BAND[0]) & (pn <= BAND[1])).mean() * 100), 3),
                                "mean_p": round(float(pn.mean()), 4),
                                "at_upper_clamp_pct": round(float((pn >= CLAMP[1] - 1e-6).mean() * 100), 3)}
        rep[w] = out
    OUT = ROOT / "analysis/reports/b/pressure_dispatch_demo.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
