"""从 b5_ledger.csv 补算 B4 过滤前口径与全窗口汇总 (只读)。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

LEDGER = Path("analysis/reports/b/b5_ledger.csv")
METRICS = Path("analysis/reports/b/b5_backtest_metrics.json")
STEP = 5


def E(g, c):
    return float(g[c].dropna().sum()) * STEP / 60.0


def V(g, c):
    return float(g[c].dropna().sum()) * STEP


def main():
    df = pd.read_csv(LEDGER)
    m = json.loads(METRICS.read_text(encoding="utf-8"))
    common = (df.b1_status == "success") & (df.b3_status == "success")      # B4 过滤前
    retained = common & df.b3_exec                                          # B4 过滤后
    out = {}
    for name, mask in (("preB4_full", common), ("postB4_retained", retained)):
        g = df[mask]
        E1, E3 = E(g, "b1_power"), E(g, "b3_power")
        V1, V3 = V(g, "b1_flow"), V(g, "b3_flow")
        out[name] = {"n": int(len(g)),
                     "b1_energy_kwh": round(E1, 2), "b3_energy_kwh": round(E3, 2),
                     "b1_supply_m3": round(V1, 1), "b3_supply_m3": round(V3, 1),
                     "b1_sec": round(E1 / V1, 6) if V1 else None,
                     "b3_sec": round(E3 / V3, 6) if V3 else None,
                     "energy_reduction_pct": round((E1 - E3) / E1 * 100, 4) if E1 else None,
                     "sec_reduction_pct": round((E1 / V1 - E3 / V3) / (E1 / V1) * 100, 4) if V1 and V3 else None}
    # 全窗口 (不可评估范围)
    out["all_window"] = {
        "n": int(len(df)),
        "b1_evaluable": int((df.b1_status == "success").sum()),
        "b3_evaluable": int(retained.sum()),
        "infeasible": int((df.b1_status != "success").sum()),
        "infeasible_required_m3_unevaluable": round(V(df[df.b1_status != "success"], "required"), 1),
    }
    m["comparison_pre_post_b4"] = out
    METRICS.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
