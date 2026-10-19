"""任务二: "大马拉小车"低负荷可减少性分析 (基于 A6 校准验证段账本, 不重跑)。

分类每个"运行且 load<0.5"的机组步:
  - single_forced : 站级需求 < 134.45 (一台机的最低非低负荷流量) -> 不推高即低负荷;
  - double_forced : 双机运行且需求 < 268.9 (=2*134.45) -> 必有一台 <0.5 (除非过供);
  - reducible     : 双机且需求 >= 268.9, 可通过再平衡两机均 >=134.45;
  - other。
另给出: 允许过供(抬到 134.45)时的理论低负荷下界与所需额外供气量。
输出: analysis/reports/b/bigsmall_analyze.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LED = ROOT / "analysis/reports/b/a6_b5_continuous_cal_ledger.csv"
OUT = ROOT / "analysis/reports/b/bigsmall_analyze.json"
STRAT = ("b1_rule", "b3_b4", "myopic", "multi_step", "v2", "cand")
STEP, RATED = 15, 268.9
NLT = 0.5 * RATED   # 134.45


def _classify(g, s):
    rows = g[g[f"{s}_exec"]]
    cls = {"single_forced": 0, "double_forced": 0, "reducible": 0, "other": 0}
    extra_supply = 0.0
    low_steps = 0
    for _, r in rows.iterrows():
        f4, f5 = float(r[f"{s}_f4"]), float(r[f"{s}_f5"])
        running = [f for f in (f4, f5) if f > 0]
        low = [f for f in running if f / RATED < 0.5]
        if not low:
            continue
        low_steps += 1
        req = float(r["obs_flow"]) if np.isfinite(r["obs_flow"]) else float(r["ub15"])
        if len(running) == 1:
            if req < NLT:
                cls["single_forced"] += 1
                extra_supply += (NLT - req) * STEP
            else:
                cls["other"] += 1   # 单机但需求>=NLT 却低: 应可提高负荷
        else:
            if req < NLT:
                cls["single_forced"] += 1
                extra_supply += (NLT - req) * STEP
            elif req < 2 * NLT:
                cls["double_forced"] += 1
                extra_supply += (2 * NLT - req) * STEP
            else:
                cls["reducible"] += 1
    return {"low_machine_steps": low_steps, "classification": cls,
            "extra_supply_to_lift_m3": round(extra_supply, 1)}


def main():
    df = pd.read_csv(LED)
    df = df[df["window"] == "valid"].reset_index(drop=True)
    rep = {"window": "valid 2026-08-01~08-20", "n_steps": int(len(df)),
           "thresholds": {"rated": RATED, "non_low_flow": NLT,
                          "note": "低负荷=运行且 flow/268.9<0.5; NLT=134.45"}, "per_strategy": {}}
    bs = {}
    for s in STRAT:
        g = df[df[f"{s}_status"] == "success"]
        low_min = float((((g[f"{s}_f4"] > 0) & (g[f"{s}_f4"] / RATED < 0.5)).sum()
                         + ((g[f"{s}_f5"] > 0) & (g[f"{s}_f5"] / RATED < 0.5)).sum()) * STEP)
        c = _classify(g, s)
        c["low_minutes_total"] = round(low_min, 1)
        rep["per_strategy"][s] = c
        bs[s] = low_min
    b1 = bs["b1_rule"]
    cand = bs["cand"]
    rep["reduction_vs_b1_pct"] = {s: (round((b1 - bs[s]) / b1 * 100, 4) if b1 > 0 else None) for s in STRAT}
    # 理论下界: 若 reducible 全部消除 (再平衡, 无需过供), 剩余 = forced
    rem = {s: rep["per_strategy"][s]["classification"]["single_forced"]
           + rep["per_strategy"][s]["classification"]["double_forced"] for s in STRAT}
    rep["forced_low_machine_steps"] = rem
    rep["max_reduction_if_eliminate_reducible_pct"] = {
        s: (round((bs[s] - rem[s]) / bs[s] * 100, 4) if bs[s] > 0 else None) for s in STRAT}
    # 加载率偏差 (任务三): 目标=额定 1.0 与 效率平台下界; 逐机 |d|>10% 比例
    TOL = 10.0
    TP = {"4#": 0.8537, "5#": 0.9381}
    lr = {}
    for s in STRAT:
        g = df[df[f"{s}_status"] == "success"]
        for tname, tgt in (("rated", 1.0), ("plateau", TP)):
            ex = 0; tot = 0; devs = []
            for d in ("4#", "5#"):
                f = g[f"{s}_f{d[0]}"].to_numpy(float); lam = f / RATED; m = f > 0
                if not m.any():
                    continue
                t = tgt[d] if isinstance(tgt, dict) else tgt
                dd = (lam[m] - t) / t * 100.0
                ex += int((np.abs(dd) > TOL).sum()); tot += int(m.sum()); devs.append(float(np.mean(np.abs(dd))))
            lr.setdefault(s, {})[tname] = {"exceed_ratio": round(ex / tot, 4) if tot else None,
                                           "mean_abs_dev_pct": round(float(np.mean(devs)), 3) if devs else None}
    rep["load_ratio"] = lr
    # 60% 可行性判断
    rep["feasibility_60pct"] = {
        "cand_low_minutes": round(cand, 1), "cand_reduction_vs_b1_pct": rep["reduction_vs_b1_pct"]["cand"],
        "cand_forced_minutes": round(rem["cand"] * STEP, 1),
        "verdict": ("60% 不可达(受单机/双机最低非低负荷流量约束)" if
                    (b1 - rem["cand"] * STEP) / b1 < 0.60 else "可能在消除可再平衡后接近")}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
