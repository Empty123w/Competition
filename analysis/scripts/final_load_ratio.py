"""加载率偏离度核实 (真实历史回测账本; 不重跑算法)。

赛题: 加载率偏离度 ≤ ±10%。官方无公式 -> 采用**内部定义**:
  加载率 lambda_d = Q_d / Q_rated,d  (Q_rated = 268.9 m³/min)
  偏离度 D = |lambda4 - lambda5|, 两台同时运行时才有定义, 以**百分点(pp)**报告。
  内部要求: D <= 10 pp (赛题 ±10% 的内部解释)。

样本处理:
  - 双机同时运行  -> 有效样本, 计入 D 统计;
  - 单机运行/全停 -> 机组间偏离度**不适用(NA)**; 不计入 D, 单独报告样本数与覆盖率;
  - (辅助) D3 = "D<=10pp 的达标步占比", 兼容旧报告, 不作为赛题主结果。

主结果: 平均 / 中位 / P95 / 最大 偏离度(pp) + 双机有效样本达标率 + 是否全部满足 <=10pp。
输出: analysis/reports/b/final/load_ratio.json + LOAD_RATIO_REPORT.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FIN = Path("analysis/reports/b/final")
RATED = 268.9
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")   # 与能效 KPI 同一 service 口径
WINDOWS = ("dev", "valid", "test")


def _scope(df):
    """service 口径: 各策略 status==success 且 exec 且 flow>=obs-0.05 (与 KPI 表一致)。"""
    common = pd.Series(True, index=df.index)
    for s in STRATS:
        common &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    svc = common.copy()
    for s in STRATS:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return svc


def _stats(g, s, limit_pp=10.0):
    f4 = g[f"{s}_f4"].to_numpy(float)
    f5 = g[f"{s}_f5"].to_numpy(float)
    both = (f4 > 0) & (f5 > 0)
    single = (f4 > 0) ^ (f5 > 0)
    none = (f4 <= 0) & (f5 <= 0)
    n = len(g)
    d = np.abs(f4[both] / RATED - f5[both] / RATED) * 100.0 if both.any() else np.array([])
    d3_full = np.ones(n, dtype=bool)
    if both.any():
        d3_full[both] = d <= limit_pp + 1e-9
    out = {
        "n_steps": int(n),
        "n_both": int(both.sum()), "n_single": int(single.sum()), "n_none": int(none.sum()),
        "both_coverage": round(float(both.mean()), 4),
        # 主结果: 偏离度 (pp) —— 仅双机有效样本
        "deviation_mean_pp": round(float(d.mean()), 3) if d.size else None,
        "deviation_median_pp": round(float(np.median(d)), 3) if d.size else None,
        "deviation_p95_pp": round(float(np.percentile(d, 95)), 3) if d.size else None,
        "deviation_max_pp": round(float(d.max()), 3) if d.size else None,
        "limit_pp": limit_pp,
        "all_within_limit": (bool(d.max() <= limit_pp + 1e-9) if d.size else None),
        # 辅助: 达标率 (兼容旧报告)
        "pass_rate_both": round(float((d <= limit_pp + 1e-9).mean()), 4) if d.size else None,
        "pass_rate_all_steps_D3": round(float(d3_full.mean()), 4),
        "NA_note": "单机/停机样本偏离度不适用(NA), 不计入 D 统计",
    }
    return out


def main():
    rep = {"definition": {
        "load_ratio": "lambda_d = Q_d / 268.9",
        "deviation": "D = |lambda4 - lambda5|, 单位=百分点(pp); 仅两台同时运行时定义",
        "target": "D <= 10 pp (赛题 ±10% 的内部解释)",
        "primary_metric": "偏离度的 平均/中位/P95/最大 (pp) + 双机有效样本达标率",
        "not_applicable": "单机运行/全停 -> D 不适用(NA), 单独报告样本数与覆盖率",
        "auxiliary": "D3 = 达标步占比 (兼容旧报告), 不作为赛题主结果",
        "official_status": "内部定义, 尚无官方确认公式"},
        "windows": {}, "pooled": {}}

    frames = {}
    for w in WINDOWS:
        df = pd.read_csv(FIN / f"backtest_{w}_ledger.csv")
        frames[w] = df
        svc = _scope(df)
        g = df.loc[svc]
        rep["windows"][w] = {"_scope_steps": int(len(g)), **{s: _stats(g, s) for s in STRATS}}

    pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
    gp = pooled.loc[_scope(pooled)]
    rep["pooled"] = {"_scope_steps": int(len(gp)), **{s: _stats(gp, s) for s in STRATS}}

    (FIN / "load_ratio.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(rep, FIN / "LOAD_RATIO_REPORT.md")
    j = rep["pooled"]["joint"]
    print(f"joint pooled: 平均 {j['deviation_mean_pp']}pp, P95 {j['deviation_p95_pp']}pp, 最大 {j['deviation_max_pp']}pp; "
          f"双机达标率 {j['pass_rate_both']}; 双机样本 {j['n_both']}/{j['n_steps']} (覆盖 {j['both_coverage']})")


def _md(rep, path):
    L = ["# 加载率偏离度 (真实历史回测; service 口径)", "",
         "## 内部定义 (官方未确认)", "",
         "- 加载率 λ_d = Q_d / 268.9 (rated)",
         "- **偏离度 D = |λ4 − λ5|**, 单位 **百分点 (pp)**; 仅**两台同时运行**时有定义",
         "- 内部要求: **D ≤ 10 pp** (赛题 ±10% 的内部解释)",
         "- 单机运行/全停: 偏离度 **不适用(NA)**, 不计入 D, 单独报告样本与覆盖率",
         "- (辅助) D3 = 达标步占比, 兼容旧报告, 不作为赛题主结果", "",
         "## 主结果: 偏离度 (pooled, dev+valid+test, service 口径)", "",
         "| 策略 | 平均pp | 中位pp | P95pp | 最大pp | 全部≤10pp | 双机达标率 | 双机样本 | 单机 | 停机 | 覆盖率 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in STRATS:
        m = rep["pooled"][s]
        L.append(f"| {s} | {m['deviation_mean_pp']} | {m['deviation_median_pp']} | {m['deviation_p95_pp']} | "
                 f"{m['deviation_max_pp']} | {m['all_within_limit']} | {m['pass_rate_both']} | {m['n_both']} | "
                 f"{m['n_single']} | {m['n_none']} | {m['both_coverage']} |")
    L += ["", "## 分窗口 (joint)", "",
          "| 窗口 | 平均pp | 中位pp | P95pp | 最大pp | 全部≤10pp | 双机达标率 | 双机样本 | 覆盖率 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for w in WINDOWS:
        m = rep["windows"][w]["joint"]
        L.append(f"| {w} | {m['deviation_mean_pp']} | {m['deviation_median_pp']} | {m['deviation_p95_pp']} | "
                 f"{m['deviation_max_pp']} | {m['all_within_limit']} | {m['pass_rate_both']} | {m['n_both']} | "
                 f"{m['both_coverage']} |")
    L += ["", "> 说明: 该公式为**当前内部定义**, 尚未获得官方确认; 若官方定义"
          "“相对目标加载率的偏离”, 应另以 |λ_d − λ_target| 评价。"]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
