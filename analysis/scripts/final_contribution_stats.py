"""模块贡献: 调度算法消融 + 配对统计 (真实历史账本; 不重跑算法)。

- service 口径, 相同步/容量/安全/功率模型/指标定义;
- pooled SEC / 低负荷 / D3 对照;
- joint vs B3 的 pooled SEC 差异 + 逐步配对 bootstrap 95% CI (检验 0.024pp 是否显著)。
输出: analysis/reports/b/final/contribution_dispatch.json + CONTRIBUTION_DISPATCH.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FIN = Path("analysis/reports/b/final")
RATED, STEP = 268.9, 15
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "b3_lrb", "joint_nompc", "mpc_hist", "joint")
WINDOWS = ("dev", "valid", "test")


def _scope(df):
    common = pd.Series(True, index=df.index)
    for s in STRATS:
        common &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    svc = common.copy()
    for s in STRATS:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return svc


def _kpi(g):
    dt_h = STEP / 60.0
    out = {}
    for s in STRATS:
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        low = float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                     + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
        f4 = g[f"{s}_f4"].to_numpy(float); f5 = g[f"{s}_f5"].to_numpy(float)
        both = (f4 > 0) & (f5 > 0)
        d = np.abs(f4[both] / RATED - f5[both] / RATED) if both.any() else np.array([])
        out[s] = {"E": E, "V": V, "sec": E / V, "low": low,
                  "d3_both": round(float((d <= 0.10).mean()), 4) if d.size else None,
                  "gap_pp_mean": round(float(d.mean() * 100), 3) if d.size else None}
    return out


def _bootstrap_sec_diff(g, a, b, B=1000, seed=0):
    """pooled SEC_b - SEC_a 的 bootstrap 95% CI (对步重采样)。"""
    dt_h = STEP / 60.0
    Ea = g[f"{a}_power"].fillna(0).to_numpy(float) * dt_h
    Va = g[f"{a}_flow"].fillna(0).to_numpy(float) * STEP
    Eb = g[f"{b}_power"].fillna(0).to_numpy(float) * dt_h
    Vb = g[f"{b}_flow"].fillna(0).to_numpy(float) * STEP
    n = len(g); rng = np.random.default_rng(seed)
    obs = Eb.sum() / Vb.sum() - Ea.sum() / Va.sum()
    diffs = np.empty(B)
    for i in range(B):
        idx = rng.integers(0, n, n)
        diffs[i] = Eb[idx].sum() / Vb[idx].sum() - Ea[idx].sum() / Va[idx].sum()
    return {"sec_diff": round(float(obs), 6),
            "ci95_low": round(float(np.percentile(diffs, 2.5)), 6),
            "ci95_high": round(float(np.percentile(diffs, 97.5)), 6),
            "p_gt_0": round(float((diffs > 0).mean()), 3)}


def main():
    frames = {w: pd.read_csv(FIN / f"backtest_{w}_ledger.csv") for w in WINDOWS}
    pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
    gp = pooled.loc[_scope(pooled)]
    k = _kpi(gp)
    b1sec = k["b1_rule"]["sec"]; b1E = k["b1_rule"]["E"]; b1low = k["b1_rule"]["low"]
    table = {}
    for s in STRATS:
        table[s] = {"sec_kwh_per_m3": round(k[s]["sec"], 6),
                    "sec_reduction_pct": round((b1sec - k[s]["sec"]) / b1sec * 100, 3),
                    "saving_pct": round((b1E - k[s]["E"]) / b1E * 100, 3),
                    "low_reduction_pct": round((b1low - k[s]["low"]) / b1low * 100, 3),
                    "d3_both": k[s]["d3_both"], "gap_pp_mean": k[s]["gap_pp_mean"],
                    "supply_vs_b1_pct": round((k[s]["V"] - k["b1_rule"]["V"]) / k["b1_rule"]["V"] * 100, 3)}

    # 分窗口 joint vs b3
    per_win = {}
    for w in WINDOWS:
        gw = frames[w].loc[_scope(frames[w])]
        kw = _kpi(gw)
        per_win[w] = {s: {"sec_reduction_pct": round((kw["b1_rule"]["sec"] - kw[s]["sec"]) / kw["b1_rule"]["sec"] * 100, 3),
                          "low_reduction_pct": round((kw["b1_rule"]["low"] - kw[s]["low"]) / kw["b1_rule"]["low"] * 100, 3),
                          "d3_both": kw[s]["d3_both"]}
                      for s in STRATS}

    stats = {
        "pooled_steps": int(len(gp)),
        "joint_vs_b3": _bootstrap_sec_diff(gp, "b3_b4", "joint"),
        "joint_vs_lrb": _bootstrap_sec_diff(gp, "lrb_t10", "joint"),
        "joint_vs_b3lrb": _bootstrap_sec_diff(gp, "b3_lrb", "joint"),
        "joint_vs_nompc": _bootstrap_sec_diff(gp, "joint_nompc", "joint"),
        "nompc_vs_b3": _bootstrap_sec_diff(gp, "b3_b4", "joint_nompc"),
    }
    rep = {"table": table, "per_window": per_win, "stats": stats,
           "note": "SEC 差异 bootstrap 对步重采样; 正值=后者 SEC 更低(更省)"}
    (FIN / "contribution_dispatch.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
