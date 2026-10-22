"""综合能效核实 (真实历史回测账本, service 口径; 不重跑算法)。

区分四类数据:
  (a) 真实 SCADA 历史观测 (obs / 真实压力)
  (b) 真实历史数据驱动的反事实模型回测 (本脚本: joint 等在执行快照下的功率模型估计)  <- 主口径
  (c) 合成闭环仿真 (stage3 CAUSAL_ACCEPTANCE: 9.85%)
  (d) 假设条件下的年度成本估算
输出: analysis/reports/b/final/energy_verify.json + ENERGY_VERIFY.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FIN = Path("analysis/reports/b/final")
STEP, RATED = 15, 268.9
PRICE = 0.70  # CNY/kWh (假设)
STRATS = ("b1_rule", "b3_b4", "bal", "lrb_t10", "mpc_hist", "joint")
WINDOWS = ("dev", "valid", "test")


def _scope(df):
    common = pd.Series(True, index=df.index)
    for s in STRATS:
        common &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].astype(bool)
    svc = common.copy()
    for s in STRATS:
        svc &= (df[f"{s}_flow"].fillna(0) >= df["obs"] - 0.05)
    return svc


def _block(g):
    dt_h = STEP / 60.0
    out = {}
    b1E = float(g["b1_rule_power"].fillna(0).sum()) * dt_h
    b1V = float(g["b1_rule_flow"].fillna(0).sum()) * STEP
    b1sec = b1E / b1V if b1V else None

    def _low(s):
        a = (g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)
        b = (g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)
        return float((a.sum() + b.sum()) * STEP)
    b1low = _low("b1_rule")
    for s in STRATS:
        E = float(g[f"{s}_power"].fillna(0).sum()) * dt_h
        V = float(g[f"{s}_flow"].fillna(0).sum()) * STEP
        sec = E / V if V else None
        out[s] = {"energy_kwh": round(E, 1), "supply_m3": round(V, 1), "sec": round(sec, 6) if sec else None,
                  "sec_reduction_pct": round((b1sec - sec) / b1sec * 100, 3) if b1sec and sec else None,
                  "saving_pct": round((b1E - E) / b1E * 100, 3) if b1E else None,
                  "supply_diff_vs_b1_m3": round(V - b1V, 1),
                  "supply_diff_vs_b1_pct": round((V - b1V) / b1V * 100, 3) if b1V else None,
                  "low_min": round(_low(s), 1),
                  "low_reduction_pct": round((b1low - _low(s)) / b1low * 100, 3) if b1low > 0 else None}
    return out


def main():
    rep = {"definitions": {
        "sec": "SEC = ΣE(kWh) / ΣV(m³); E 由 B2 功率模型在执行流量下估计",
        "sec_reduction": "(SEC_b1 - SEC_x)/SEC_b1 * 100",
        "saving_same_supply": "(E_b1 - E_x)/E_b1 * 100 (前提: 服务等价, 见下)",
        "service_equiv": "逐步要求 flow_x >= obs - 0.05 m³/min (沿用 B5 定义; obs 为真实 SCADA 总供气量代理)",
        "obs_nature": "obs 为真实 SCADA 站级总供气量, 作为历史代理需求; 非独立实测用户侧需求",
        "scope": "service 口径: 所有比较策略在该步均成功执行且满足服务等价"},
        "windows": {}, "pooled": {}, "synthetic_closed_loop": {},
        "cost": {"price_cny_per_kwh": PRICE,
                 "formula": "年成本降幅 ≈ 服务等价节电率 (同服务下电耗降幅); 非直接等同 SEC 降幅",
                 "assumption": "电价恒定, 负荷/运行时长年化不变, 节电率与窗口一致 (线性)"}}
    frames = {}
    for w in WINDOWS:
        df = pd.read_csv(FIN / f"backtest_{w}_ledger.csv")
        frames[w] = df
        svc = df.loc[_scope(df)]
        rep["windows"][w] = {"n_steps": int(len(svc)), "_block": _block(svc)}
    pooled = pd.concat([frames[w] for w in WINDOWS], ignore_index=True)
    gp = pooled.loc[_scope(pooled)]
    rep["pooled"] = {"n_steps": int(len(gp)), "_block": _block(gp)}

    # 合成闭环 (第三阶段 causal) 作为对照
    c = json.loads((Path("analysis/reports/b/stage3/causal_acceptance.json")).read_text(encoding="utf-8"))
    rep["synthetic_closed_loop"] = {
        "source": "analysis/reports/b/stage3/causal_acceptance.json (合成 5 场景闭环, test 种子 3,4)",
        "joint_sec_reduction_pct": c["pooled"]["joint"]["sec_reduction_vs_b1_pct"],
        "joint_low_reduction_pct": c["pooled"]["joint"]["low_reduction_vs_b1_pct"],
        "note": "合成需求场景, 非真实历史; 仅作方法对照, 不作主展示口径"}

    (FIN / "energy_verify.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _md(rep, FIN / "ENERGY_VERIFY.md")
    print("pooled n:", rep["pooled"]["n_steps"])
    for s in STRATS:
        m = rep["pooled"]["_block"][s]
        print(f"  {s:9s} SEC={m['sec']} SECdn={m['sec_reduction_pct']}% save={m['saving_pct']}% "
              f"dV%={m['supply_diff_vs_b1_pct']}")


def _md(rep, path):
    L = ["# 综合能效核实 (真实历史回测; service 口径)", "",
         "## 口径与数据性质", ""]
    for k, v in rep["definitions"].items():
        L.append(f"- **{k}**: {v}")
    L += ["", "## 分窗口 SEC 改善 (joint)", "",
          "| 窗口 | 样本步 | SEC(kWh/m³) | SEC降% | 同服务节电% | Δ供气%vs b1 |", "|---|---|---|---|---|---|"]
    for w in WINDOWS:
        b = rep["windows"][w]["_block"]; j = b["joint"]
        L.append(f"| {w} | {rep['windows'][w]['n_steps']} | {j['sec']} | {j['sec_reduction_pct']} | "
                 f"{j['saving_pct']} | {j['supply_diff_vs_b1_pct']} |")
    p = rep["pooled"]["_block"]["joint"]
    L.append(f"| **pooled** | {rep['pooled']['n_steps']} | {p['sec']} | {p['sec_reduction_pct']} | "
             f"{p['saving_pct']} | {p['supply_diff_vs_b1_pct']} |")
    L += ["", "## 池化策略对照 (真实历史, service 口径)", "",
          "| 策略 | SEC降% | 同服务节电% | Δ供气%vs b1 |", "|---|---|---|---|"]
    for s in STRATS:
        m = rep["pooled"]["_block"][s]
        L.append(f"| {s} | {m['sec_reduction_pct']} | {m['saving_pct']} | {m['supply_diff_vs_b1_pct']} |")
    sc = rep["synthetic_closed_loop"]
    L += ["", "## 与合成闭环结果的区别", "",
          f"- 真实历史回测 (主口径): pooled SEC 降 **{p['sec_reduction_pct']}%**, 同服务节电 **{p['saving_pct']}%**",
          f"- 合成闭环 (对照, 非主展示): joint SEC 降 **{sc['joint_sec_reduction_pct']}%** ({sc['source']})",
          f"- 年度成本: 约 **{p['saving_pct']}%** × 电价 {rep['cost']['price_cny_per_kwh']} 元/kWh (假设, 见 cost.formula)"]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
