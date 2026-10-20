"""回退步审计 + 评估口径核查 (只读账本)。

分类:
  - executed        : status==success & exec==True (真实执行, 计入真实能耗)
  - blocked_safety  : status==success & exec==False (B4/喘振/供气阻断; 计划被否决)
  - infeasible      : status!=success (容量/状态不可行, 无合法动作)
口径:
  - full           : 全窗 fillna(0) (含未执行计划值 -> 不可比)
  - success        : 仅 status==success 步 (含被阻断计划值 -> 非真实)
  - exec_paired    : 全策略 exec 且 status==success (可执行配对 -> 可比)
  - service        : exec_paired 且各策略 flow>=obs-0.05
输出: analysis/reports/b/fallback_audit.json
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEP, RATED = 15, 268.9
DT_H = STEP / 60.0


def audit(window, ledger, strategies):
    d = pd.read_csv(ROOT / ledger)
    n = len(d)
    rep = {"window": window, "n": n, "classify": {}, "scopes": {}}
    for s in strategies:
        succ = d[f"{s}_status"].eq("success")
        ex = d[f"{s}_exec"].fillna(False)
        rep["classify"][s] = {
            "executed": int((succ & ex).sum()),
            "blocked_safety": int((succ & ~ex).sum()),
            "infeasible": int((~succ).sum()),
        }
    def block(idx):
        g = d.loc[idx]
        out = {"steps": int(len(g))}
        b1E = float(g["b1_rule_power"].fillna(0).sum()) * DT_H
        b1V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
        b1sec = b1E / b1V if b1V else None
        out["b1_sec"] = round(b1sec, 6) if b1sec else None
        out["obs_mean"] = round(float(g["obs"].mean()), 1)
        for s in strategies:
            E = float(g[f"{s}_power"].fillna(0).sum()) * DT_H
            V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
            sec = E / V if V else None
            out[s] = {"E": round(E, 1), "V": round(V, 1), "sec": round(sec, 6) if sec else None,
                      "sec_red_pct": round((b1sec - sec) / b1sec * 100, 4) if b1sec and sec else None,
                      "save_pct": round((b1E - E) / b1E * 100, 4) if b1E else None,
                      "dV": round(V - b1V, 1)}
        return out
    # full (fillna0)
    rep["scopes"]["full_fillna0"] = block(d.index)
    # success only
    m = pd.Series(True, index=d.index)
    for s in strategies:
        m &= d[f"{s}_status"].eq("success")
    rep["scopes"]["success_all"] = block(d.index[m])
    # exec paired
    m2 = pd.Series(True, index=d.index)
    for s in strategies:
        m2 &= d[f"{s}_status"].eq("success") & d[f"{s}_exec"].fillna(False)
    rep["scopes"]["exec_paired"] = block(d.index[m2])
    # service
    m3 = m2.copy()
    for s in strategies:
        m3 &= d[f"{s}_flow"].fillna(0) >= d["obs"] - 0.05
    rep["scopes"]["service_equiv"] = block(d.index[m3])
    # 被阻断计划的"幽灵能耗"
    ghost = {}
    for s in strategies:
        succ = d[f"{s}_status"].eq("success"); ex = d[f"{s}_exec"].fillna(False)
        gb = d.loc[succ & ~ex]
        ghost[s] = {"steps": int(len(gb)),
                    "planned_E_kwh": round(float(gb[f"{s}_power"].fillna(0).sum()) * DT_H, 1),
                    "obs_mean": round(float(gb["obs"].mean()), 1)}
    rep["blocked_planned_ghost"] = ghost
    return rep


def main():
    S = ["b1_rule", "b3_b4", "bal"]
    rep = {"note": "full(success) 含未执行计划值, 仅 exec_paired 可比; 保留旧账本",
           "dev": audit("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv", S),
           "valid": audit("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv", S)}
    OUT = ROOT / "analysis/reports/b/fallback_audit.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
