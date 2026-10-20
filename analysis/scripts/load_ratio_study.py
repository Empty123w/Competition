"""加载率专项: 三种口径定义 + 可行性边界 + 候选达标比例。

口径(全部作为候选定义, 不预设唯一正式口径):
  D1  vs 额定加载率        : λ*=1.0
  D2  vs 最佳效率加载率    : λ*=argmin SEC(λ), 由 B2 功率模型求出 (per device)
  D3  机组间加载率差        : 双机运行时的 |λ4-λ5| (均衡性), 目标 0
偏离: D1/D2 相对百分比 = (λ-λ*)/λ*×100; 达标 = |偏离|≤10%。
适用样本: 运行且 flow∈[120,268.9]。
输出: analysis/reports/b/load_ratio_study.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config

ROOT = Path(__file__).resolve().parents[2]
RATED, LO, HI, TOL = 268.9, 120.0, 268.9, 10.0
STRAT = ["b1_rule", "b3_b4", "bal"]
MODEL = load_optimization_config()["paths"]["power_model"]


def best_eff_lambda(device, lo=LO, hi=HI, step=0.5):
    flows = np.arange(lo, hi + 1e-6, step)
    best = None
    for f in flows:
        r = PERF.estimate_power(device, float(f), model_path=MODEL)
        if r["status"] != "success" or not f:
            continue
        sec = r["estimated_power_kw"] / f
        if best is None or sec < best[0]:
            best = (sec, float(f))
    return {"lambda_star": round(best[1] / RATED, 4), "sec_min_kwh_per_m3": round(best[0], 6),
            "sec_monotone_decreasing": None}


def dev_stats(f: pd.Series, target: float):
    q = f[(f > 0) & (f >= LO - 1e-6) & (f <= HI + 1e-6)]
    if len(q) == 0:
        return {"n": 0}
    lam = q / RATED
    dev = (lam - target) / target * 100.0
    return {"n": int(len(q)), "lambda_mean": round(float(lam.mean()), 4),
            "dev_mean_pct": round(float(dev.mean()), 3),
            "dev_absmax_pct": round(float(dev.abs().max()), 3),
            "exceed_ratio": round(float((dev.abs() > TOL).mean()), 4),
            "meet_ratio": round(float((dev.abs() <= TOL).mean()), 4)}


def inter_machine(f4: pd.Series, f5: pd.Series):
    on = (f4 > 0) & (f5 > 0)
    l4 = (f4 / RATED)[on]; l5 = (f5 / RATED)[on]
    if len(l4) == 0:
        return {"n_double": 0}
    spread = (l4 - l5).abs()
    return {"n_double": int(len(l4)), "spread_mean": round(float(spread.mean()), 4),
            "spread_p95": round(float(spread.quantile(.95)), 4),
            "spread_max": round(float(spread.max()), 4),
            "within_10pp_ratio": round(float((spread <= 0.10).mean()), 4)}


def feasibility(target_lambda_by_dev, reserve_frac=0.05, min_flow=120.0):
    """对需求网格, 判定是否存在(组合+分配)使全部运行机 λ 在目标 ±10% 内且满足需求+备用。"""
    rows = []
    for d in np.arange(0, 2 * RATED + 1, 5.0):
        need = d * (1 + reserve_frac)
        feas = False
        note = ""
        # 单机
        for dev in ("4#", "5#"):
            tgt = target_lambda_by_dev[dev] * RATED
            lo_t, hi_t = tgt * (1 - TOL / 100), tgt * (1 + TOL / 100)
            lo_f, hi_f = max(min_flow, lo_t), min(RATED, hi_t)
            cap = RATED
            if cap + 1e-6 >= need and lo_f - 1e-6 <= d <= hi_f + 1e-6:
                feas = True; note = f"single:{dev}"
        # 双机: 各自在 [max(min,lo_t), min(RATED,hi_t)]
        if not feas:
            l4 = target_lambda_by_dev["4#"] * RATED; l5 = target_lambda_by_dev["5#"] * RATED
            lo4, hi4 = max(min_flow, l4 * (1 - TOL / 100)), min(RATED, l4 * (1 + TOL / 100))
            lo5, hi5 = max(min_flow, l5 * (1 - TOL / 100)), min(RATED, l5 * (1 + TOL / 100))
            if 2 * RATED + 1e-6 >= need and lo4 + lo5 - 1e-6 <= d <= hi4 + hi5 + 1e-6:
                feas = True; note = "double"
        rows.append({"obs": round(float(d), 1), "feasible": bool(feas), "note": note})
    df = pd.DataFrame(rows)
    return {"feasible_ratio_over_0_2R": round(float(df.feasible.mean()), 4),
            "feasible_ranges": _ranges(df)}


def _ranges(df):
    out = []
    cur = None
    for _, r in df.iterrows():
        if r.feasible and cur is None:
            cur = [r.obs]
        elif not r.feasible and cur is not None:
            out.append([cur[0], prev]); cur = None
        prev = r.obs
    if cur is not None:
        out.append([cur[0], prev])
    return out


def main():
    lam_star = {d: best_eff_lambda(d)["lambda_star"] for d in ("4#", "5#")}
    tgt_D1 = {"4#": 1.0, "5#": 1.0}
    tgt_D2 = lam_star
    rep = {"definitions": {
        "D1_vs_rated": {"target": tgt_D1, "basis": "工程假设 (赛题未注明确目标)"},
        "D2_vs_best_eff": {"target": tgt_D2, "basis": "B2 功率模型 argmin SEC",
                           "detail": {d: best_eff_lambda(d) for d in ("4#", "5#")}},
        "D3_inter_machine_spread": {"target": 0.0, "unit": "pp",
                                    "basis": "机组间均衡性; 目标 0 为工程解释"},
        "tolerance_pct": TOL, "official_status": "三项均未经甲方确认 (任务书 §2.3-4)"}}
    rep["feasibility"] = {
        "D1": feasibility(tgt_D1),
        "D2": feasibility(tgt_D2),
    }
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        m = pd.Series(True, index=df.index)
        for s in STRAT:
            m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        g0 = df.loc[m]
        out = {}
        for s in STRAT:
            f = {d: g0[f"{s}_f{d[0]}"] for d in ("4#", "5#")}
            out[s] = {
                "D1": {"4#": dev_stats(f["4#"], tgt_D1["4#"]), "5#": dev_stats(f["5#"], tgt_D1["5#"])},
                "D2": {"4#": dev_stats(f["4#"], tgt_D2["4#"]), "5#": dev_stats(f["5#"], tgt_D2["5#"])},
                "D3": inter_machine(f["4#"], f["5#"]),
            }
        rep[w] = out
    OUT = ROOT / "analysis/reports/b/load_ratio_study.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
