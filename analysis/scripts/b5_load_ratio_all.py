"""加载率 D1/D3 三窗口×4策略 (test 补充)。输出 analysis/reports/b/delivery/load_ratio_all.json"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RATED = 268.9
STRAT = ["b1_rule", "b3_b4", "bal", "lrb_t10"]
SEGS = {
    "dev": "analysis/reports/b/multikpi_dev_tol_ledger.csv",
    "valid": "analysis/reports/b/multikpi_valid_tol_ledger.csv",
    "test": "analysis/reports/b/multikpi_test_0_3000_ledger.csv",
}
TGT_A = {"4#": 1.0, "5#": 1.0}


def main():
    rep = {"definition": {"D1": "相对额定 λ*=1.0 ±10% (单机统计)", "D3": "机组间 |λ4-λ5|<=0.10 (pp)",
                          "official_status": "官方未给数学定义 (待确认)"}, "segments": {}}
    for name, f in SEGS.items():
        df = pd.read_csv(ROOT / f)
        m = pd.Series(True, index=df.index)
        for s in STRAT:
            m &= df[f"{s}_status"].eq("success") & df[f"{s}_exec"].fillna(False)
        g = df.loc[m]
        out = {"paired_steps": int(len(g))}
        for s in STRAT:
            d1 = 0; tot = 0
            for d in ("4#", "5#"):
                q = g[f"{s}_f{d[0]}"]
                q = q[(q >= 120 - 1e-6) & (q <= RATED + 1e-6)]
                dv = (q / RATED - TGT_A[d]) / TGT_A[d]
                d1 += int((dv.abs() <= 0.10).sum()); tot += int(len(q))
            on = (g[f"{s}_f4"] > 0) & (g[f"{s}_f5"] > 0)
            sp = ((g[f"{s}_f4"] - g[f"{s}_f5"]).abs() / RATED)[on]
            out[s] = {"D1_meet_ratio": round(d1 / tot, 4) if tot else None,
                      "D3_within10pp_ratio": round(float((sp <= 0.10).mean()), 4) if len(sp) else None,
                      "D3_spread_mean": round(float(sp.mean()), 4) if len(sp) else None}
        rep["segments"][name] = out
    OUT = ROOT / "analysis/reports/b/delivery/load_ratio_all.json"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
