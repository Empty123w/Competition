"""B 算法独立实验 (不覆盖冻结 B1-B5; 不在 B5 窗口调参)。

E1 分段功率曲线负荷分配: 开发窗口拟合, 验证窗口比较误差与分配能耗
E2 多时间步滚动优化: 使用**仅过去信息**的临时预测 (季节日) 与 oracle 上界诊断

任务口径: 站级实测总流量 (nowcast), 仅用于本实验; 非 CAL-02 冻结口径
输出: analysis/reports/b/algorithm_experiments.json + algorithm_experiments_ledger.csv
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

OUT = Path("analysis/reports/b/algorithm_experiments.json")
LED = Path("analysis/reports/b/algorithm_experiments_ledger.csv")
VAL_START, VAL_END = "2026-08-01", "2026-08-20"
STEP = 15
DAY = 1440


def _fit_piecewise(x, y):
    best = None
    for q in np.linspace(0.3, 0.8, 11):
        k = float(np.quantile(x, q))
        L, R = x <= k, x > k
        if L.sum() < 30 or R.sum() < 30:
            continue
        al, bl = np.polyfit(x[L], y[L], 1)
        ar, br = np.polyfit(x[R], y[R], 1)
        pred = np.where(L, al * x + bl, ar * x + br)
        sse = float(np.sum((y - pred) ** 2))
        if best is None or sse < best[0]:
            best = (sse, {"knot": k, "al": al, "bl": bl, "ar": ar, "br": br})
    return best[1]


def _pw_predict(spec, x):
    return np.where(x <= spec["knot"], spec["al"] * x + spec["bl"], spec["ar"] * x + spec["br"])


def _metrics(y, p):
    y = np.asarray(y, float); p = np.asarray(p, float); e = p - y
    return {"MAE": round(float(np.mean(np.abs(e))), 3), "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "MAPE": round(float(np.mean(np.abs(e) / np.maximum(np.abs(y), 1.0)) * 100), 3), "n": int(len(y))}


def main():
    t0 = time.perf_counter()
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    base_model = oc["paths"]["power_model"]
    pcfg = PERF.load_config()
    samples = PERF.load_samples()
    device_df, station_df = PIPE.load_processed()
    flow_at = dict(zip(station_df["time"].astype(str), station_df["total_flow_m3_min"]))

    # ---------- E1: 分段功率曲线 ----------
    pw = {}
    e1_cmp = {}
    for did in ("4#", "5#"):
        fit, _ = PERF.screen_samples(samples, did, pcfg)
        m = PERF.split_masks(fit["time"], pcfg)
        tr, va = fit[m["train"]], fit[m["valid"]]
        x, y = tr["flow_m3_min"].to_numpy(), tr["power_kw"].to_numpy()
        spec = _fit_piecewise(x, y)
        pv = _pw_predict(spec, va["flow_m3_min"].to_numpy())
        art = json.load(open(base_model, encoding="utf-8"))
        base_pred = PERF.predict_model(art["devices"][did]["params"], va)
        pw[did] = spec
        e1_cmp[did] = {"base_valid": _metrics(va["power_kw"], base_pred),
                       "piecewise_valid": _metrics(va["power_kw"], pv)}

    def pw_est(did, flow):
        s = pw[did]
        return {"status": "success", "estimated_power_kw": float(_pw_predict(s, np.array([flow]))[0]), "warnings": []}

    def base_power(flows):
        tot, ok = 0.0, True
        for d, q in flows.items():
            if q and q > 0:
                r = PERF.estimate_power(d, q, model_path=base_model)
                if r["status"] == "success": tot += r["estimated_power_kw"]
                else: ok = False
        return tot if ok else None

    def plan(required_now, required_cap, caps, estimator):
        need = required_cap + con.reserve.extra_m3_min(required_cap)
        best = None
        for a in CONS.enumerate_combinations(required_cap, caps, con.reserve):
            if not a.feasible:
                continue
            alloc = OPT.allocate_flow(a.combo, required_now, caps, base_model, estimate=estimator) \
                if estimator else OPT.allocate_flow(a.combo, required_now, caps, base_model)
            if not alloc["ok"]:
                continue
            if best is None or alloc["power_kw"] < best[0]:
                best = (alloc["power_kw"], a.combo, alloc["flows"])
        return best

    times = sorted(station_df["time"])
    val = [t for t in times if VAL_START <= str(t)[:10] <= VAL_END][::STEP]
    rows = []
    for t in val:
        ts = str(t)
        task = float(flow_at.get(ts, np.nan))
        if not np.isfinite(task) or task <= 0:
            continue
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        caps = CONS.build_capabilities(con, snap, [], None)
        # 冻结 B3 (nowcast)
        fc = C.ForecastResult(ts, 60, task, task, task, "nowcast")
        r3 = optimize_dispatch(snap, fc, [], con)
        f3 = {a.device_id: (a.target_flow_m3_min or 0.0) for a in r3.device_actions if a.running_after}
        # E1 分段分配
        b1 = plan(task, task, caps, pw_est)
        # E2 临时预测 (季节日, 仅过去) 与 oracle(诊断, 非部署)
        season_now = float(flow_at.get(str(t - pd.Timedelta(minutes=DAY)), np.nan))
        season_hz = [flow_at.get(str(t - pd.Timedelta(minutes=DAY) + pd.Timedelta(minutes=15 * h)), np.nan) for h in range(1, 5)]
        season_hz = [v for v in season_hz if np.isfinite(v)]
        season_cap = max([season_now] + season_hz) if np.isfinite(season_now) else None
        myopic = plan(season_now, season_now, caps, None) if season_now and season_now > 0 else None
        rolling = plan(season_now, season_cap, caps, None) if season_now and season_now > 0 else None
        actual_hz = [float(flow_at.get(str(t + pd.Timedelta(minutes=15 * h)), np.nan)) for h in range(0, 5)]
        actual_hz = [v for v in actual_hz if np.isfinite(v)]
        oracle = plan(task, max(actual_hz), caps, None) if actual_hz else None
        rows.append({"t": ts, "task": task,
                     "b3_energy": base_power(f3) if f3 else None, "b3_flow": sum(f3.values()),
                     "e1_energy": base_power(b1[2]) if b1 else None, "e1_flow": sum(b1[2].values()) if b1 else None,
                     "myopic_energy": base_power(myopic[2]) if myopic else None,
                     "rolling_energy": base_power(rolling[2]) if rolling else None,
                     "oracle_energy": base_power(oracle[2]) if oracle else None,
                     "b3_n": len(f3), "e1_n": len(b1[1]) if b1 else 0,
                     "myopic_n": len(myopic[1]) if myopic else 0, "rolling_n": len(rolling[1]) if rolling else 0,
                     "season_ok": bool(season_now and season_now > 0)})
    df = pd.DataFrame(rows)
    df.to_csv(LED, index=False)
    dt = STEP / 60.0

    def esummary(col):
        g = df.dropna(subset=[col])
        return {"n": int(len(g)), "energy_kwh": round(float(g[col].sum()) * dt, 2)}

    rep = {"protocol": {"valid_window": f"{VAL_START}~{VAL_END}", "step_minutes": STEP,
                        "task": "observed station total_flow (nowcast, 仅实验)", "reserve": "frozen extra 5%",
                        "uses_B5_final_window": False,
                        "evidence_scope": "开发/验证窗口 + 临时过去信息预测; 非 A 冻结口径, 非达标证据"},
           "e1_model": e1_cmp,
           "energy_by_plan": {k: esummary(k) for k in
                              ("b3_energy", "e1_energy", "myopic_energy", "rolling_energy", "oracle_energy")},
           "n_steps": int(len(df)), "runtime_s": round(time.perf_counter() - t0, 1)}
    # 相对 B3 的变化
    common = df.dropna(subset=["b3_energy", "e1_energy"])
    if len(common):
        rep["e1_vs_b3_pct"] = round((common["b3_energy"].sum() - common["e1_energy"].sum()) / common["b3_energy"].sum() * 100, 4)
    mc = df.dropna(subset=["myopic_energy", "rolling_energy"])
    if len(mc):
        rep["rolling_vs_myopic_pct"] = round((mc["myopic_energy"].sum() - mc["rolling_energy"].sum()) / mc["myopic_energy"].sum() * 100, 4)
        rep["rolling_changed_combo_steps"] = int((mc["rolling_n"] != mc["myopic_n"]).sum())
    oc_ = df.dropna(subset=["myopic_energy", "oracle_energy"])
    if len(oc_):
        rep["oracle_vs_myopic_pct"] = round((oc_["myopic_energy"].sum() - oc_["oracle_energy"].sum()) / oc_["myopic_energy"].sum() * 100, 4)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
