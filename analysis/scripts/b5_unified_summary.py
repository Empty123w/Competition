"""阶段三: dev+valid(+test) 全历史统一汇总 (同一 4 策略口径, 分段不连续不强行拼接)。

策略: b1_rule(B1) / b3_b4(B3+B4) / bal / lrb_t10。
对每段:
  - full_window : 全窗 fillna0 (各策略自有供气步不同步; 仅参考, 标注不可比)
  - paired_exec : 4 策略 status==success 且 exec==True 的可比子集
  - service_equiv: paired_exec 且各策略 flow>=obs-0.05
跨段汇总: 对同一口径在各段分别求 E,V,low,再**求和后重算** SEC/节能率 (不平均百分比)。
输出: analysis/reports/b/b5_unified_summary.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEP, RATED = 15, 268.9
DT_H = STEP / 60.0
STRAT = ["b1_rule", "b3_b4", "bal", "lrb_t10"]
SEGS = {
    "dev": ("analysis/reports/b/multikpi_dev_tol_ledger.csv", "2026-03-26", "2026-07-31"),
    "valid": ("analysis/reports/b/multikpi_valid_tol_ledger.csv", "2026-08-01", "2026-08-20"),
    "test": ("analysis/reports/b/multikpi_test_0_3000_ledger.csv", "2026-08-21", "2026-09-12"),
}


def low_min(g, s):
    return float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                  + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)


def agg(g, s):
    E = float(g[f"{s}_power"].fillna(0).sum()) * DT_H
    V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
    return E, V, low_min(g, s)


def seg_scopes(df):
    out = {}
    out["full_window"] = df.index
    m = pd.Series(True, index=df.index)
    for s in STRAT:
        m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
    out["paired_exec"] = df.index[m]
    m2 = m.copy()
    for s in STRAT:
        m2 &= df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05
    out["service_equiv"] = df.index[m2]
    return out


def load_seg(path):
    df = pd.read_csv(ROOT / path)
    for s in STRAT:
        assert f"{s}_status" in df.columns, f"{path} 缺 {s}"
    return df


def main():
    raw = {}
    for name, (path, a, b) in SEGS.items():
        df = load_seg(path)
        raw[name] = df
    rep = {"definition": {
        "strategies": STRAT, "dry_run_note": "full_window 各策略自有供气步不同步, 不可直接比; 以 paired_exec/service_equiv 为准",
        "sec": "ΣE(kWh)/ΣV(m3)", "saving_same_supply": "(E_b1-E_x)/E_b1", "low": "运行且 flow/268.9<0.5 的机分钟*15(min)",
        "pooling": "各段同口径求和后重算, 不平均百分比"},
        "segments": {}, "pooled": {}}

    # 每段
    seg_store = {}
    for name, df in raw.items():
        scopes = seg_scopes(df)
        rec = {"range": [SEGS[name][1], SEGS[name][2]], "n_steps": int(len(df)),
               "obs_mean": round(float(df["obs"].mean()), 1),
               "obs_p95": round(float(df["obs"].quantile(.95)), 1),
               "obs_max": round(float(df["obs"].max()), 1),
               "infeasible": {s: int(df[f"{s}_status"].ne("success").sum()) for s in STRAT},
               "blocked": {s: int((df[f"{s}_status"].eq("success") & ~df[f"{s}_exec"].fillna(False)).sum()) for s in STRAT},
               "scopes": {}}
        store = {}
        for sc, idx in scopes.items():
            g = df.loc[idx]
            b1E, b1V, b1low = agg(g, "b1_rule")
            blk = {"steps": int(len(g)), "b1": {"E": round(b1E, 1), "V": round(b1V, 1),
                    "SEC": round(b1E / b1V, 6) if b1V else None, "low_min": b1low}}
            for s in STRAT[1:]:
                E, V, lm = agg(g, s)
                blk[s] = {"E": round(E, 1), "V": round(V, 1), "SEC": round(E / V, 6) if V else None,
                          "sec_reduction_pct": round((b1E / b1V - E / V) / (b1E / b1V) * 100, 4) if b1V and V else None,
                          "saving_same_supply_pct": round((b1E - E) / b1E * 100, 4) if b1E else None,
                          "low_min": lm,
                          "low_reduction_pct": round((b1low - lm) / b1low * 100, 4) if b1low > 0 else None,
                          "gap_m3": round(float((g["obs"].fillna(0) - g[f"{s}_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 1),
                          "dS_vs_b1_m3": round(V - b1V, 1)}
            store[sc] = blk
        rec["scopes"] = store
        seg_store[name] = store
        rep["segments"][name] = rec

    # 汇总
    def pool(names, scope):
        tot = {s: {"E": 0.0, "V": 0.0, "low": 0.0, "gap": 0.0} for s in STRAT}
        steps = 0
        for n in names:
            blk = seg_store[n][scope]
            steps += blk["steps"]
            for s in STRAT:
                key = "b1" if s == "b1_rule" else s
                d = blk[key]
                tot[s]["E"] += d["E"]; tot[s]["V"] += d["V"]; tot[s]["low"] += d["low_min"]
                tot[s]["gap"] += d.get("gap_m3", 0.0)
        out = {"steps": steps}
        b1 = tot["b1_rule"]
        out["b1"] = {"E": round(b1["E"], 1), "V": round(b1["V"], 1), "SEC": round(b1["E"] / b1["V"], 6),
                     "low_min": round(b1["low"], 1)}
        for s in STRAT[1:]:
            t = tot[s]
            out[s] = {"E": round(t["E"], 1), "V": round(t["V"], 1),
                      "SEC": round(t["E"] / t["V"], 6) if t["V"] else None,
                      "sec_reduction_pct": round((b1["E"] / b1["V"] - t["E"] / t["V"]) / (b1["E"] / b1["V"]) * 100, 4) if t["V"] else None,
                      "saving_same_supply_pct": round((b1["E"] - t["E"]) / b1["E"] * 100, 4),
                      "low_min": round(t["low"], 1),
                      "low_reduction_pct": round((b1["low"] - t["low"]) / b1["low"] * 100, 4) if b1["low"] else None,
                      "gap_m3": round(t["gap"], 1), "dS_vs_b1_m3": round(t["V"] - b1["V"], 1)}
        return out

    for scope in ("full_window", "paired_exec", "service_equiv"):
        rep["pooled"][f"dev_valid_{scope}"] = pool(["dev", "valid"], scope)
        rep["pooled"][f"dev_valid_test_{scope}"] = pool(["dev", "valid", "test"], scope)

    OUT = ROOT / "analysis/reports/b/b5_unified_summary.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    # 打印关键
    for k, v in rep["pooled"].items():
        print("==", k, "steps", v["steps"])
        for s in STRAT[1:]:
            print("   %-8s SECred %7s low_red %8s save %7s dS %s" % (s, v[s]["sec_reduction_pct"],
                  v[s]["low_reduction_pct"], v[s]["saving_same_supply_pct"], v[s]["dS_vs_b1_m3"]))
    print("written", OUT)


if __name__ == "__main__":
    main()
