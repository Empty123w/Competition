"""加载率偏离度核实 (真实历史回测账本; 不重跑算法)。

D3 定义 (B 组内部, 复核自 analysis/scripts/b_final_backtest.py::kpis):
  λ_d = flow_d / 268.9 (rated 268.9 m³/min)
  逐步: 非双机运行记达标; 双机运行则 |λ4-λ5| <= 0.10 记达标
  D3_all  = mean(达标步 / 全部步)      <- 当前报告口径 (含单机/停机步)
  D3_both = mean(达标 / 双机运行步)

本脚本在**与 KPI 表一致的样本范围** (service 口径 = 各策略均成功执行且 flow>=obs-0.05)
上补充: 双机运行步 |λ4-λ5| 的 mean / max / P95 / 中位, 以及 ≤10pp 达标率。
输出: analysis/reports/b/final/load_ratio.json + LOAD_RATIO_REPORT.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FIN = Path("analysis/reports/b/final")
RATED = 268.9
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")
WINDOWS = ("dev", "valid", "test")


def _scope(df):
    """service 口径索引: 各策略 status==success 且 exec 且 flow>=obs-0.05。"""
    common = pd.Series(True, index=df.index)
    for s in STRATS:
        common &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    svc = common.copy()
    for s in STRATS:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return common, svc


def _stats(g, s):
    f4 = g[f"{s}_f4"].to_numpy(float); f5 = g[f"{s}_f5"].to_numpy(float)
    both = (f4 > 0) & (f5 > 0)
    single = (f4 > 0) ^ (f5 > 0)
    none = (f4 <= 0) & (f5 <= 0)
    d = np.abs(f4[both] / RATED - f5[both] / RATED) if both.any() else np.array([])
    d3 = np.ones(len(g), dtype=bool)
    if both.any():
        d3[both] = d <= 0.10
    return {"n_steps": int(len(g)), "n_both": int(both.sum()), "n_single": int(single.sum()),
            "n_none": int(none.sum()), "d3_all": round(float(d3.mean()), 4),
            "d3_both": (round(float((d <= 0.10).mean()), 4) if d.size else None),
            "gap_mean_pp": (round(float(d.mean() * 100), 3) if d.size else None),
            "gap_max_pp": (round(float(d.max() * 100), 3) if d.size else None),
            "gap_p95_pp": (round(float(np.percentile(d, 95) * 100), 3) if d.size else None),
            "gap_median_pp": (round(float(np.median(d) * 100), 3) if d.size else None)}


def main():
    rep = {"definition": {
        "load_ratio": "lambda_d = flow_d / 268.9 (rated)",
        "diff": "|lambda4 - lambda5| (0-1 分数; 报告以百分点 pp = x100)",
        "d3_all": "逐步: 非双机记达标; 双机则 |Δλ|<=0.10 记达标; D3_all=均值 (当前报告口径)",
        "d3_both": "仅双机运行步的 |Δλ|<=0.10 达标率",
        "scope": "service 口径 (各策略均成功执行且 flow>=obs-0.05), 与 KPI 表一致",
        "note": "D3=1.0 表示内部达标率 100% (所有评价步满足), 不代表偏离度=1%"},
        "windows": {}, "pooled": {}}
    frames = {}
    for w in WINDOWS:
        df = pd.read_csv(FIN / f"backtest_{w}_ledger.csv")
        frames[w] = df
        _, svc = _scope(df)
        g = df.loc[svc]
        rep["windows"][w] = {"_scope_steps": int(len(g)),
                             **{s: _stats(g, s) for s in STRATS}}
    pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
    _, svc = _scope(pooled)
    gp = pooled.loc[svc]
    rep["pooled"] = {"_scope_steps": int(len(gp)), **{s: _stats(gp, s) for s in STRATS}}

    (FIN / "load_ratio.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(rep, FIN / "LOAD_RATIO_REPORT.md")
    print("service scope steps:", {w: rep["windows"][w]["_scope_steps"] for w in WINDOWS},
          "pooled", rep["pooled"]["_scope_steps"])
    for s in STRATS:
        m = rep["pooled"][s]
        print(f"  {s:9s} both={m['n_both']} mean={m['gap_mean_pp']}pp P95={m['gap_p95_pp']} max={m['gap_max_pp']} "
              f"D3_both={m['d3_both']} D3_all={m['d3_all']}")


def _md(rep, path):
    L = ["# 加载率偏离度核实 (真实历史回测; service 口径)", "",
         "## 公式 (内部 D3)", "",
         "- λ_d = flow_d / 268.9 (rated 268.9 m³/min, 无量纲 0–1)",
         "- 机组间偏差 = |λ4 − λ5|, 以**百分点 (pp)** 报告 (分数 ×100)",
         "- 逐步判定: 非双机运行记达标; 双机运行则 |λ4−λ5| ≤ 0.10 (10pp) 记达标",
         "- D3_all = 达标步 / 全部评价步 (当前报告口径); D3_both = 达标 / 双机运行步",
         "- **D3=1.0 表示内部达标率 100%, 不是偏离度 1%**", "",
         f"- service 口径样本数: dev {rep['windows']['dev']['_scope_steps']}, "
         f"valid {rep['windows']['valid']['_scope_steps']}, test {rep['windows']['test']['_scope_steps']}, "
         f"pooled {rep['pooled']['_scope_steps']}", "",
         "## 池化对照 (dev+valid+test, service 口径)", "",
         "| 策略 | 双机步 | 单机步 | 停机步 | 平均偏差pp | 中位pp | P95pp | 最大pp | ≤10pp达标率 | D3_all |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for s in STRATS:
        m = rep["pooled"][s]
        L.append(f"| {s} | {m['n_both']} | {m['n_single']} | {m['n_none']} | {m['gap_mean_pp']} | "
                 f"{m['gap_median_pp']} | {m['gap_p95_pp']} | {m['gap_max_pp']} | {m['d3_both']} | {m['d3_all']} |")
    L += ["", "## 分窗口 (joint)", "",
          "| 窗口 | 双机步 | 平均pp | 中位pp | P95pp | 最大pp | ≤10pp达标率 | D3_all |",
          "|---|---|---|---|---|---|---|---|"]
    for w in WINDOWS:
        m = rep["windows"][w]["joint"]
        L.append(f"| {w} | {m['n_both']} | {m['gap_mean_pp']} | {m['gap_median_pp']} | {m['gap_p95_pp']} | "
                 f"{m['gap_max_pp']} | {m['d3_both']} | {m['d3_all']} |")
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
