"""理论最小功率 (oracle split) 与 B3 实际功率对比: 判断内部优化天花板。

对每个步, 需求 = obs (真实交付需求), 枚举 f4/f5 的可行分配
  f in {0} ∪ [min_stable, RATED], f4+f5 >= need
求 min P4(f4)+P5(f5)。与 b3_b4/bal 实际功率比较。
输出: analysis/reports/b/oracle_split.json + _sample.csv
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.optimization import performance as PERF

ROOT = Path(__file__).resolve().parents[2]
STEP, RATED, NLT = 15, 268.9, 134.45
DT_H = STEP / 60.0
MODEL = None


def pow_of(flows, model, cache):
    tot = 0.0
    for d, q in flows.items():
        if q and q > 0:
            k = (d, round(float(q), 1))
            if k not in cache:
                e = PERF.estimate_power(d, float(q), model_path=model)
                cache[k] = e["estimated_power_kw"] if e["status"] == "success" else None
            if cache[k] is None:
                return None
            tot += cache[k]
    return tot


def oracle(need, model, cache, step=2.0):
    best = None
    lo = NLT
    for f4 in [0.0] + list(np.arange(lo, RATED + 1e-6, step)):
        for f5 in [0.0] + list(np.arange(lo, RATED + 1e-6, step)):
            if f4 + f5 + 1e-6 < need:
                continue
            pr = pow_of({d: q for d, q in (("4#", f4), ("5#", f5)) if q > 0}, model, cache)
            if pr is None:
                continue
            if best is None or pr < best[0]:
                best = (pr, float(f4), float(f5))
    return best


def main():
    global MODEL
    oc_path = PIPE.load_processed and None
    from air_station.optimization.service import load_optimization_config
    model = load_optimization_config()["paths"]["power_model"]
    cache = {}
    rep = {}
    samples = {}
    for w, ledger in (("dev", "analysis/reports/b/multikpi_dev_0_13000_ledger.csv"),
                      ("valid", "analysis/reports/b/multikpi_valid_0_2000_ledger.csv")):
        d = pd.read_csv(ROOT / ledger)
        m = d[d["b1_rule_status"].eq("success") & d["b1_rule_exec"].fillna(False)
              & d["b3_b4_status"].eq("success") & d["b3_b4_exec"].fillna(False)
              & d["bal_status"].eq("success") & d["bal_exec"].fillna(False)].copy()
        rng = np.random.default_rng(0)
        idx = rng.choice(m.index, size=min(400, len(m)), replace=False)
        rows = []
        for i in idx:
            r = m.loc[i]
            need = float(r["obs"])
            if need <= 0:
                continue
            o = oracle(need, model, cache)
            if o is None:
                continue
            rows.append({"t": r["t"], "obs": need, "oracle_kw": o[0], "o_f4": o[1], "o_f5": o[2],
                         "b3_kw": float(r["b3_b4_power"]), "bal_kw": float(r["bal_power"]),
                         "b3_f4": float(r["b3_b4_f4"]), "b3_f5": float(r["b3_b4_f5"])})
        s = pd.DataFrame(rows)
        s["b3_excess_pct"] = (s.b3_kw - s.oracle_kw) / s.oracle_kw * 100
        s["bal_excess_pct"] = (s.bal_kw - s.oracle_kw) / s.oracle_kw * 100
        samples[w] = s
        rep[w] = {
            "sampled": int(len(s)),
            "oracle_E_kwh": round(float(s.oracle_kw.sum()) * DT_H, 1),
            "b3_E_kwh": round(float(s.b3_kw.sum()) * DT_H, 1),
            "bal_E_kwh": round(float(s.bal_kw.sum()) * DT_H, 1),
            "b3_excess_pct_mean": round(float(s.b3_excess_pct.mean()), 4),
            "bal_excess_pct_mean": round(float(s.bal_excess_pct.mean()), 4),
            "b3_excess_pct_p95": round(float(s.b3_excess_pct.quantile(.95)), 4),
            "oracle_sec_headroom_b3_pct": round((s.b3_kw.sum() - s.oracle_kw.sum()) / s.b3_kw.sum() * 100, 4),
        }
        s.to_csv(ROOT / f"analysis/reports/b/oracle_split_{w}_sample.csv", index=False)
    OUT = ROOT / "analysis/reports/b/oracle_split.json"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
