"""加载率可行性: 每步理论最优 vs 候选实际 (分离不可行与可改善)。

对每步需求 d, 枚举 (组合 × 分配网格), 在满足 需求+extra备用/容量/min/max 的可行集内求:
  oracle_spread = min |λ4-λ5| (双机)
  oracle_maxdev = min max_d |λd - 1|  (D1)
比较 b3/bal 实际达成值 -> 可改善量。
输出: analysis/reports/b/load_ratio_feasibility.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config

ROOT = Path(__file__).resolve().parents[2]
RATED, MINF, RESERVE = 268.9, 120.0, 0.05
MODEL = load_optimization_config()["paths"]["power_model"]
_cache: dict = {}


def _p(d, q):
    k = (d, round(float(q), 1))
    if k not in _cache:
        r = PERF.estimate_power(d, float(q), model_path=MODEL)
        _cache[k] = r["estimated_power_kw"] if r["status"] == "success" else None
    return _cache[k]


def oracle(d, grid=2.0):
    need = d * (1 + RESERVE)
    best_spread, best_maxdev = None, None
    # 单机
    for dev in ("4#", "5#"):
        if RATED + 1e-6 >= need and MINF - 1e-6 <= d <= RATED + 1e-6:
            lam = d / RATED
            dev_v = abs(lam - 1.0)
            best_maxdev = dev_v if best_maxdev is None else min(best_maxdev, dev_v)
    # 双机
    if 2 * RATED + 1e-6 >= need:
        for f4 in np.arange(MINF, RATED + 1e-6, grid):
            f5 = d - f4
            if f5 < MINF - 1e-6 or f5 > RATED + 1e-6:
                continue
            if _p("4#", f4) is None or _p("5#", f5) is None:
                continue
            sp = abs(f4 - f5) / RATED
            md = max(abs(f4 / RATED - 1.0), abs(f5 / RATED - 1.0))
            best_spread = sp if best_spread is None else min(best_spread, sp)
            best_maxdev = md if best_maxdev is None else min(best_maxdev, md)
    return best_spread, best_maxdev


def main():
    rep = {"def": {"oracle_spread": "min |λ4-λ5| (双机)", "oracle_maxdev": "min max|λ-1|",
                   "reserve": RESERVE, "min_flow": MINF, "grid": 2.0,
                   "note": "oracle 在可行集内枚举; 与候选实际对比得出可改善量"}}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        df = pd.read_csv(ROOT / ledger)
        m = df[(df.b1_rule_status.eq("success")) & df.b3_b4_status.eq("success") & df.bal_status.eq("success")]
        rng = np.random.default_rng(1)
        idx = rng.choice(m.index, size=min(300, len(m)), replace=False)
        rows = []
        for i in idx:
            r = m.loc[i]; d = float(r["obs"])
            if d <= 0:
                continue
            sp, md = oracle(d)
            rows.append({"t": r["t"], "obs": d, "oracle_spread": sp, "oracle_maxdev": md,
                         "b3_spread": abs(float(r["b3_b4_f4"]) - float(r["b3_b4_f5"])) / RATED if r["b3_b4_f4"] > 0 and r["b3_b4_f5"] > 0 else None,
                         "bal_spread": abs(float(r["bal_f4"]) - float(r["bal_f5"])) / RATED if r["bal_f4"] > 0 and r["bal_f5"] > 0 else None})
        s = pd.DataFrame(rows)
        double = s[s.oracle_spread.notna() & s.bal_spread.notna()]
        def gap(col_or, col_cand):
            return round(float((double[col_cand] - double[col_or]).clip(lower=0).mean()), 4) if len(double) else None
        rep[w] = {
            "sampled": int(len(s)),
            "oracle_maxdev_mean": round(float(s.oracle_maxdev.dropna().mean()), 4),
            "oracle_maxdev_ge10pct_ratio": round(float((s.oracle_maxdev.dropna() > 0.10).mean()), 4),
            "oracle_spread_mean_double": round(float(double.oracle_spread.mean()), 4) if len(double) else None,
            "b3_spread_mean_double": round(float(double.b3_spread.mean()), 4) if len(double) else None,
            "bal_spread_mean_double": round(float(double.bal_spread.mean()), 4) if len(double) else None,
            "b3_gap_vs_oracle_mean": gap("oracle_spread", "b3_spread"),
            "bal_gap_vs_oracle_mean": gap("oracle_spread", "bal_spread"),
            "double_steps": int(len(double)),
        }
        s.to_csv(ROOT / f"analysis/reports/b/load_ratio_feasibility_{w}_sample.csv", index=False)
    OUT = ROOT / "analysis/reports/b/load_ratio_feasibility.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
