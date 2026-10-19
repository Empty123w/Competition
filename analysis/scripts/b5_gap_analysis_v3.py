"""B5 前置分析 v3: 补充核查 (只读, 不改 B1-B4 算法)。

- 逐机预计功率 (B1/B3), 与额定/工程上限比较
- 共同可行步持续时间与平均功率
- 同供气匹配集 vs B4 保留集 的筛选条件/交集/差集
- 功率模型流量系数的 OLS 标准误 (排序稳健性可复算依据)

用法: python3 analysis/scripts/b5_gap_analysis_v3.py [step_minutes]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

OUT_JSON = Path("analysis/reports/b/b5_gap_analysis_v3.json")
OUT_STEPS = Path("analysis/reports/b/b5_per_step_v3.csv")
SINGLE_RATED = 268.9
SINGLE_WITH_EXTRA = 268.9 / 1.05


def _b1_dev_power(base, model):
    out = {"4#": None, "5#": None}
    for a in base.device_actions:
        if a.running_after:
            r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0, model_path=model)
            out[a.device_id] = r["estimated_power_kw"] if r["status"] == "success" else None
    return out


def backtest(step_minutes=5):
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    cap = float(oc["dispatch"].get("power_cap_kw_per_device", 1600.0))
    device_df, station_df = PIPE.load_processed()
    forecasts = PIPE.load_frozen_forecasts(horizon_minutes=base_cfg["demand"]["horizon_minutes"])
    sample = sorted(forecasts)[::step_minutes]
    recs = []
    for t in sample:
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        f = forecasts[t]
        base = B1.decide(snap, f, [], con, base_cfg, C.PolicyState())
        opt = optimize_dispatch(snap, f, [], con)
        b1d = _b1_dev_power(base, model) if base.status == "success" else {"4#": None, "5#": None}
        b3d = {"4#": None, "5#": None}
        for a in opt.device_actions:
            if a.running_after:
                b3d[a.device_id] = a.estimated_power_kw
        recs.append({"t": str(t), "b1_status": base.status, "b3_status": opt.status,
                     "b1_flow": base.total_flow_m3_min, "b3_flow": opt.total_flow_m3_min,
                     "b1_power": (b1d["4#"] or 0) + (b1d["5#"] or 0) if base.status == "success" else None,
                     "b3_power": opt.estimated_power_kw if opt.status == "success" else None,
                     "b1_p4": b1d["4#"], "b1_p5": b1d["5#"], "b3_p4": b3d["4#"], "b3_p5": b3d["5#"],
                     "b3_blocked": bool(opt.safety_blocked), "b3_surge": opt.surge_status,
                     "required": f.demand_value(con.demand_basis)})
    return pd.DataFrame(recs), model, cap


def _pstats(series):
    s = pd.Series(series).dropna()
    if not len(s):
        return {}
    return {"n": int(len(s)), "min": round(float(s.min()), 2), "p50": round(float(s.median()), 2),
            "max": round(float(s.max()), 2),
            "gt_rated_1250": int((s > 1250).sum()), "gt_rated_pct": round(float((s > 1250).mean()) * 100, 3),
            "gt_cap_1600": int((s > 1600).sum()), "gt_cap_pct": round(float((s > 1600).mean()) * 100, 3)}


def slope_se(fit_df, features, y):
    X = fit_df[features].to_numpy(dtype=float)
    if np.isnan(X).any():
        means = np.nanmean(X, axis=0)
        idx = np.isnan(X)
        X[idx] = np.take(means, np.where(idx)[1])
    A = np.hstack([X, np.ones((len(X), 1))])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    n, p = A.shape
    sigma2 = float(resid @ resid) / (n - p)
    XtX_inv = np.linalg.inv(A.T @ A)
    se = np.sqrt(np.diag(XtX_inv) * sigma2)
    return {"coef": [float(c) for c in coef], "se": [float(s) for s in se],
            "flow_coef": float(coef[0]), "flow_se": float(se[0]), "n": int(n)}


def main():
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    t0 = time.perf_counter()
    df, model, cap = backtest(step)
    if step == 5:
        df.to_csv(OUT_STEPS, index=False)
    common = (df.b1_status == "success") & (df.b3_status == "success")
    c = df[common].copy()
    n = int(len(c))
    duration_h = n * step / 60.0
    E1 = float(c.b1_power.dropna().sum()) * step / 60.0
    E3 = float(c.b3_power.dropna().sum()) * step / 60.0

    rep = {"step_minutes": step, "common_steps": n,
           "duration_h": round(duration_h, 3),
           "b1_energy_kwh": round(E1, 2), "b3_energy_kwh": round(E3, 2),
           "b1_avg_power_kw": round(E1 / duration_h, 2), "b3_avg_power_kw": round(E3 / duration_h, 2),
           "rated_total_kw": 2500.0, "cap_per_device_kw": cap,
           "per_device_power": {
               "b1_4#": _pstats(c.b1_p4), "b1_5#": _pstats(c.b1_p5),
               "b3_4#": _pstats(c.b3_p4), "b3_5#": _pstats(c.b3_p5)},
           "runtime_s": round(time.perf_counter() - t0, 1)}

    # 同供气匹配 vs B4 保留
    matched = set(c.loc[(c.b1_flow - c.b3_flow).abs() <= 1.0, "t"])
    retained = set(c.loc[~c.b3_blocked, "t"])
    inter = matched & retained
    rep["set_analysis"] = {
        "matched_criterion": "|b1_flow - b3_flow| <= 1.0 m3/min (事后选定, 未预注册)",
        "retained_criterion": "B4 safety_blocked == False",
        "matched_n": len(matched), "retained_n": len(retained),
        "intersection_n": len(inter),
        "matched_only_n": len(matched - retained), "retained_only_n": len(retained - matched),
    }
    for name, s in (("matched", matched), ("retained", retained), ("intersection", inter)):
        g = c[c.t.isin(s)]
        if len(g):
            V1 = float(g.b1_flow.sum()) * step; V3 = float(g.b3_flow.sum()) * step
            e1 = float(g.b1_power.sum()) * step / 60.0; e3 = float(g.b3_power.sum()) * step / 60.0
            rep.setdefault("set_metrics", {})[name] = {
                "n": len(g), "b1_energy_kwh": round(e1, 2), "b3_energy_kwh": round(e3, 2),
                "b1_sec": round(e1 / V1, 6), "b3_sec": round(e3 / V3, 6),
                "energy_reduction_pct": round((e1 - e3) / e1 * 100, 4),
                "sec_reduction_pct": round((e1 / V1 - e3 / V3) / (e1 / V1) * 100, 4)}

    # 流量系数 OLS 标准误 (排序稳健性可复算依据)
    samples = PERF.load_samples()
    pcfg = PERF.load_config()
    art = json.load(open(model, encoding="utf-8"))
    rep["slope_uncertainty"] = {}
    for did in ("4#", "5#"):
        feat = art["devices"][did]["features"]
        fit, _ = PERF.screen_samples(samples, did, pcfg)
        m = PERF.split_masks(fit["time"], pcfg)
        fv = pd.concat([fit[m["train"]], fit[m["valid"]]], ignore_index=True)
        rep["slope_uncertainty"][did] = slope_se(fv, feat, fv["power_kw"].to_numpy())

    OUT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:5000])


if __name__ == "__main__":
    main()
