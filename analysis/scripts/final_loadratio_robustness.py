"""加载率约束稳健性对照: joint_nompc 10pp (硬约束) vs 9pp (内部保守阈值)。

同数据/口径; 只改 joint_nompc 的 spread_tol, 其余逻辑/模型/约束不变。
输出: analysis/reports/b/final/loadratio_robustness.json + LOADRATIO_ROBUSTNESS.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FIN = Path("analysis/reports/b/final")
RATED, STEP = 268.9, 15
WINDOWS = ("dev", "valid", "test")


def _scope(df, strats):
    m = pd.Series(True, index=df.index)
    for s in strats:
        m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    svc = m.copy()
    for s in strats:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return svc


def _stats(g):
    dt_h = STEP / 60.0
    E = float(g["joint_nompc_power"].fillna(0).sum()) * dt_h
    V = float(g["joint_nompc_flow"].fillna(0).sum()) * STEP
    b1E = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
    b1V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
    b1low = _low(g, "b1_rule")
    f4 = g["joint_nompc_f4"].to_numpy(float); f5 = g["joint_nompc_f5"].to_numpy(float)
    both = (f4 > 0) & (f5 > 0); single = (f4 > 0) ^ (f5 > 0)
    d = np.abs(f4[both] / RATED - f5[both] / RATED) * 100 if both.any() else np.array([])
    blocked = int((g["joint_nompc_status"].eq("success") & ~g["joint_nompc_exec"].astype(bool)).sum())
    infeas = int((~g["joint_nompc_status"].eq("success")).sum())
    return {
        "steps": int(len(g)),
        "sec_kwh_per_m3": round(E / V, 6) if V else None,
        "sec_reduction_pct": round((b1E / b1V - E / V) / (b1E / b1V) * 100, 3) if V and b1V else None,
        "energy_saving_equalV_pct": round((b1E - E) / b1E * 100, 3) if b1E else None,
        "low_reduction_pct": round((b1low - _low(g, "joint_nompc")) / b1low * 100, 3) if b1low else None,
        "deviation_mean_pp": round(float(d.mean()), 3) if d.size else None,
        "deviation_p95_pp": round(float(np.percentile(d, 95)), 3) if d.size else None,
        "deviation_max_pp": round(float(d.max()), 3) if d.size else None,
        "both": int(both.sum()), "single": int(single.sum()), "none": int((~both & ~single).sum()),
        "both_coverage": round(float(both.mean()), 4),
        "service_gap_m3": round(float((g["obs"].fillna(0) - g["joint_nompc_flow"].fillna(0)).clip(lower=0).sum()) * STEP, 1),
        "supply_diff_vs_b1_pct": round((V - b1V) / b1V * 100, 3) if b1V else None,
        "blocked_steps": blocked, "infeasible_steps": infeas,
    }


def _low(g, s):
    a = (g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)
    b = (g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)
    return float((a.sum() + b.sum()) * STEP)


def _load(tag):
    fr = {}
    rt = {}
    for w in WINDOWS:
        f = FIN / f"backtest_{w}{tag}_ledger.csv"
        fr[w] = pd.read_csv(f)
        j = json.loads((FIN / f"backtest_{w}{tag}.json").read_text(encoding="utf-8"))
        rt[w] = j["runtime_ms"]
    return fr, rt


def main():
    fr10, rt10 = _load("")
    fr09, rt09 = _load("_tol09")
    rep = {"meta": {"validation_mode": "simulation", "joint_nompc_only": True,
                    "v10": "spread_tol=0.10 (硬约束)", "v09": "spread_tol=0.09 (内部保守阈值)",
                    "scope": "service (b1_rule & joint_nompc 共同可执行 + flow>=obs-0.05)",
                    "note": "赛题要求不变 (±10%); 9pp 仅为算法内部保守控制阈值"},
           "windows": {}, "pooled": {}}
    for tag, frames in (("v10", fr10), ("v09", fr09)):
        pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
        gp = pooled.loc[_scope(pooled, ("b1_rule", "joint_nompc"))]
        rep["pooled"][tag] = {"_steps": int(len(gp)), **_stats(gp)}
        rep["pooled"][tag]["decide_p50_ms"] = round(float(np.mean([rt10[w]["joint_nompc"]["p50"] for w in WINDOWS])), 2) if tag == "v10" else round(float(np.mean([rt09[w]["joint_nompc"]["p50"] for w in WINDOWS])), 2)
    for w in WINDOWS:
        rep["windows"][w] = {}
        for tag, frames in (("v10", fr10), ("v09", fr09)):
            g = frames[w].loc[_scope(frames[w], ("b1_rule", "joint_nompc"))]
            rep["windows"][w][tag] = _stats(g)
    (FIN / "loadratio_robustness.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(rep, FIN / "LOADRATIO_ROBUSTNESS.md")
    print(json.dumps(rep["pooled"], ensure_ascii=False, indent=1))
    for w in WINDOWS:
        a = rep["windows"][w]["v10"]; b = rep["windows"][w]["v09"]
        print(w, "SEC10", a["sec_reduction_pct"], "SEC09", b["sec_reduction_pct"],
              "| max10", a["deviation_max_pp"], "max09", b["deviation_max_pp"],
              "| low10", a["low_reduction_pct"], "low09", b["low_reduction_pct"])


def _md(rep, path):
    L = ["# 加载率约束稳健性对照 (joint_nompc; 10pp vs 9pp)", "",
         "> 仅改 joint_nompc 的 `spread_tol` (0.10 → 0.09), 其余逻辑/模型/约束不变; 赛题要求仍为 ±10%。",
         "> 口径: service (b1_rule 与 joint_nompc 共同可执行且 flow≥obs−0.05); 单机/停机为 NA。", "",
         "## 池化对照", "",
         "| 版本 | 步数 | SEC降% | 同服务节电% | 低负荷降% | 均pp | P95pp | 最大pp | 双机覆盖 | 供气缺口m³ | 不可行步 | 阻断步 | p50ms |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for tag in ("v10", "v09"):
        m = rep["pooled"][tag]
        L.append(f"| {tag} | {m['_steps']} | {m['sec_reduction_pct']} | {m['energy_saving_equalV_pct']} | "
                 f"{m['low_reduction_pct']} | {m['deviation_mean_pp']} | {m['deviation_p95_pp']} | {m['deviation_max_pp']} | "
                 f"{m['both_coverage']} | {m['service_gap_m3']} | {m['infeasible_steps']} | {m['blocked_steps']} | {m['decide_p50_ms']} |")
    L += ["", "## 分窗口", "",
          "| 窗口 | 版本 | SEC降% | 低负荷降% | 均pp | P95pp | 最大pp | 双机覆盖 | 不可行步 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        for tag in ("v10", "v09"):
            m = rep["windows"][w][tag]
            L.append(f"| {w} | {tag} | {m['sec_reduction_pct']} | {m['low_reduction_pct']} | {m['deviation_mean_pp']} | "
                     f"{m['deviation_p95_pp']} | {m['deviation_max_pp']} | {m['both_coverage']} | {m['infeasible_steps']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
