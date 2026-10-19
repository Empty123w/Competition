"""B5 补充核查: 4#/5# 功率模型载荷相关残差与边际斜率 (只读)。

输出: analysis/reports/b/b5_residual_check.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.optimization import performance as P

OUT = Path("analysis/reports/b/b5_residual_check.json")


def main():
    cfg = P.load_config()
    df = P.load_samples()
    art = json.load(open("artifacts/models/power_model.json", encoding="utf-8"))
    rep = {}
    for did in ("4#", "5#"):
        fit, _ = P.screen_samples(df, did, cfg)
        m = P.split_masks(fit["time"], cfg)
        te = fit[m["test"]]
        pred = P.predict_model(art["devices"][did]["params"], te)
        resid = te["power_kw"].to_numpy() - pred
        q = te["flow_m3_min"].quantile([1 / 3, 2 / 3]).tolist()
        lo = resid[te["flow_m3_min"] <= q[0]]
        mid = resid[(te["flow_m3_min"] > q[0]) & (te["flow_m3_min"] <= q[1])]
        hi = resid[te["flow_m3_min"] > q[1]]
        span = float(te.loc[te["flow_m3_min"] > q[1], "flow_m3_min"].mean()
                     - te.loc[te["flow_m3_min"] <= q[0], "flow_m3_min"].mean())
        rep[did] = {
            "test_n": int(len(te)),
            "resid_mean": round(float(np.mean(resid)), 3),
            "resid_std": round(float(np.std(resid)), 3),
            "corr_resid_flow": round(float(np.corrcoef(resid, te["flow_m3_min"])[0, 1]), 4),
            "resid_tercile_lo_mid_hi": [round(float(np.mean(lo)), 2), round(float(np.mean(mid)), 2),
                                        round(float(np.mean(hi)), 2)],
            "flow_tercile_span_m3min": round(span, 2),
            "slope_bias_est_kw_per_m3min": round(float((np.mean(hi) - np.mean(lo)) / span), 4),
            "flow_coef_point": round(float(art["devices"][did]["params"]["coef"][0]), 4),
        }
    rep["note"] = "残差=真实-预测; slope_bias_est 为三分位残差差/流量差的粗略指示, 非标准误"
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
