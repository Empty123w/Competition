"""≥6% 改进方案并列实验 (独立/可回退; 不覆盖冻结基线, 不使用 B5 最终测试窗口)。

方案:
  S2 逐机功率模型精化 (可执行实验)
  S1 压力设定点联合优化 (仿真情景 + 数据需求)
  S3 鲁棒前瞻调度 (数据可行性评估)
输出: analysis/reports/b/improvement_experiments.json
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
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import optimizer as OPT
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

OUT = Path("analysis/reports/b/improvement_experiments.json")
EXP_MODEL = Path("analysis/reports/b/power_model_exp_v2.json")
VAL_START, VAL_END = "2026-08-01", "2026-08-20"
STEP = 15
BAND = (0.55, 0.66)


def _refined_spec(fit_df, features, y):
    X = fit_df[features].to_numpy(dtype=float)
    means = np.nanmean(X, axis=0)
    idx = np.isnan(X)
    X[idx] = np.take(means, np.where(idx)[1])
    A = np.hstack([X, np.ones((len(X), 1))])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return {"type": "lin", "features": features, "coef": coef[:-1].tolist(),
            "intercept": float(coef[-1]), "means": means.tolist()}


def _pred(spec, df_row_flow, aux):
    x = []
    for f in spec["features"]:
        if f == "flow": x.append(df_row_flow)
        elif f == "flow2": x.append(df_row_flow ** 2)
        elif f == "flow_p": x.append(df_row_flow * aux["pressure"])
        else: x.append(aux.get(f, spec["means"][spec["features"].index(f)]))
    return float(np.dot(spec["coef"], x) + spec["intercept"])


def _metrics(y, p):
    y = np.asarray(y, float); p = np.asarray(p, float)
    e = p - y
    return {"MAE": round(float(np.mean(np.abs(e))), 3), "RMSE": round(float(np.sqrt(np.mean(e ** 2))), 3),
            "MAPE": round(float(np.mean(np.abs(e) / np.maximum(np.abs(y), 1.0)) * 100), 3), "n": int(len(y))}


def main():
    t0 = time.perf_counter()
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    base_model = oc["paths"]["power_model"]
    art = json.load(open(base_model, encoding="utf-8"))
    pcfg = PERF.load_config()
    samples = PERF.load_samples()

    # ---------- S2: 模型精化 ----------
    refined = {}
    model_cmp = {}
    val_cache = {}
    for did in ("4#", "5#"):
        fit, _ = PERF.screen_samples(samples, did, pcfg)
        m = PERF.split_masks(fit["time"], pcfg)
        tr, va = fit[m["train"]], fit[m["valid"]]
        ytr, yva = tr["power_kw"].to_numpy(), va["power_kw"].to_numpy()
        # 冻结基线模型在验证段
        base_pred = PERF.predict_model(art["devices"][did]["params"], va)
        # 精化模型: flow, flow^2, pressure, igv, ambient, flow*pressure (在开发段拟合并做特征变换)
        feats = ["flow", "flow2", "discharge_pressure_mpa", "igv_percent", "ambient_temp_c", "flow_p"]
        def make_X(df):
            X = pd.DataFrame({"flow": df["flow_m3_min"], "flow2": df["flow_m3_min"] ** 2,
                              "discharge_pressure_mpa": df["discharge_pressure_mpa"],
                              "igv_percent": df["igv_percent"], "ambient_temp_c": df["ambient_temp_c"]})
            X["flow_p"] = df["flow_m3_min"] * df["discharge_pressure_mpa"]
            return X[feats]
        trX = make_X(tr)
        means = trX.mean().to_dict()
        Xtr = trX.fillna(means).to_numpy(); A = np.hstack([Xtr, np.ones((len(Xtr), 1))])
        coef, *_ = np.linalg.lstsq(A, ytr, rcond=None)
        spec = {"type": "lin", "features": feats, "coef": coef[:-1].tolist(),
                "intercept": float(coef[-1]), "means": [means[f] for f in feats]}
        vaX = make_X(va).fillna(means).to_numpy()
        ref_pred = vaX @ np.array(spec["coef"]) + spec["intercept"]
        refined[did] = spec
        model_cmp[did] = {"base_valid": _metrics(yva, base_pred), "refined_valid": _metrics(yva, ref_pred)}
        val_cache[did] = va
    EXP_MODEL.write_text(json.dumps({"devices": refined, "validation_mode": "simulation",
                                     "note": "实验模型, 不覆盖冻结 power_model.json"}, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    def refined_est(did, flow):
        s = refined[did]
        feats, means = s["features"], s["means"]
        pm = means[feats.index("discharge_pressure_mpa")] if "discharge_pressure_mpa" in feats else 0.6
        x = []
        for f in feats:
            if f == "flow":
                x.append(flow)
            elif f == "flow2":
                x.append(flow ** 2)
            elif f == "flow_p":
                x.append(flow * pm)
            else:
                x.append(means[feats.index(f)])
        pw = float(np.dot(s["coef"], x) + s["intercept"])
        return {"status": "success", "estimated_power_kw": pw, "warnings": []}

    def plan_power_base(flows, model):
        tot, ok = 0.0, True
        for did, q in flows.items():
            if q and q > 0:
                r = PERF.estimate_power(did, q, model_path=model)
                if r["status"] == "success": tot += r["estimated_power_kw"]
                else: ok = False
        return tot if ok else None

    # ---------- 供给任务回放 (validation window, nowcast task) ----------
    device_df, station_df = PIPE.load_processed()
    times = sorted(station_df["time"])
    val_times = [t for t in times if VAL_START <= str(t)[:10] <= VAL_END][::STEP]
    rows = []
    for t in val_times:
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        st_row = station_df[station_df["time"] == t]
        task = float(st_row["total_flow_m3_min"].iloc[0]) if len(st_row) else None
        if task is None or not np.isfinite(task) or task <= 0:
            continue
        fc = C.ForecastResult(str(t), 60, task, task, task, "nowcast")
        # B1
        r1 = B1.decide(snap, fc, [], con, base_cfg, C.PolicyState())
        f1 = {a.device_id: (a.target_flow_m3_min or 0.0) for a in r1.device_actions if a.running_after}
        # B3 冻结
        r3 = optimize_dispatch(snap, fc, [], con)
        f3 = {a.device_id: (a.target_flow_m3_min or 0.0) for a in r3.device_actions if a.running_after}
        # S2 精化规划器 (同枚举+贪心, 用精化模型)
        caps = CONS.build_capabilities(con, snap, [], None)
        best = None
        for a in CONS.enumerate_combinations(task, caps, con.reserve):
            if not a.feasible:
                continue
            alloc = OPT.allocate_flow(a.combo, task, caps, base_model, estimate=refined_est)
            if not alloc["ok"]:
                continue
            if best is None or alloc["power_kw"] < best[0]:
                best = (alloc["power_kw"], alloc["flows"])
        f2 = best[1] if best else None
        rows.append({"t": str(t), "task": task,
                     "b1_ok": r1.status == "success", "b3_ok": r3.status == "success", "s2_ok": f2 is not None,
                     "b1_energy_base": plan_power_base(f1, base_model),
                     "b3_energy_base": plan_power_base(f3, base_model),
                     "s2_energy_base": plan_power_base(f2, base_model) if f2 else None,
                     "b1_flow": sum(f1.values()), "b3_flow": sum(f3.values()),
                     "s2_flow": sum(f2.values()) if f2 else None,
                     "b1_n": len(f1), "b3_n": len(f3), "s2_n": len(f2) if f2 else 0,
                     "pressure": float(st_row["header_pressure_mpa"].iloc[0]),
                     "b3_surge": r3.surge_status, "b3_blocked": bool(r3.safety_blocked)})
    df = pd.DataFrame(rows)
    dt = STEP / 60.0

    def agg(col_e, col_f):
        g = df.dropna(subset=[col_e, col_f])
        E = float(g[col_e].sum()) * dt; V = float(g[col_f].sum()) * STEP
        return {"n": int(len(g)), "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
                "sec": round(E / V, 6) if V else None,
                "shortfall_m3": round(float((g["task"] - g[col_f]).clip(lower=0).sum()) * STEP, 2)}
    common = df[df.b1_ok & df.b3_ok & df.s2_ok]
    rep = {"protocol": {"dev_window": "2026-03-12~07-31", "valid_window": f"{VAL_START}~{VAL_END}",
                        "step_minutes": STEP, "task": "observed station total_flow (nowcast)",
                        "reserve": "frozen extra 5%", "uses_B5_final_window": False},
           "s2_model_comparison": model_cmp,
           "supply_task_summary": {"n_valid_steps": int(len(df)),
                                   "b1": agg("b1_energy_base", "b1_flow"),
                                   "b3": agg("b3_energy_base", "b3_flow"),
                                   "s2": agg("s2_energy_base", "s2_flow"),
                                   "common_n": int(len(common)),
                                   "common_b1_energy": round(float(common["b1_energy_base"].sum()) * dt, 2),
                                   "common_b3_energy": round(float(common["b3_energy_base"].sum()) * dt, 2),
                                   "common_s2_energy": round(float(common["s2_energy_base"].sum()) * dt, 2),
                                   "common_reduction_b3_pct": round((common["b1_energy_base"].sum() - common["b3_energy_base"].sum()) / common["b1_energy_base"].sum() * 100, 3),
                                   "common_reduction_s2_pct": round((common["b1_energy_base"].sum() - common["s2_energy_base"].sum()) / common["b1_energy_base"].sum() * 100, 3)},
           "runtime_s": round(time.perf_counter() - t0, 1)}
    df.to_csv(Path("analysis/reports/b/improvement_experiments_ledger.csv"), index=False)
    from collections import Counter
    rep["feasibility"] = {"n": int(len(df)), "b1_ok": int(df.b1_ok.sum()), "b3_ok": int(df.b3_ok.sum()),
                          "s2_ok": int(df.s2_ok.sum()), "b3_blocked": int(df.b3_blocked.sum()),
                          "all_ok": int((df.b1_ok & df.b3_ok & df.s2_ok).sum())}
    rep["switches"] = {pr: int((((df[f"{pr}_n"] > 0).astype(int)).diff().abs() == 1).sum())
                       for pr in ("b1", "b3", "s2")}
    rep["pressure"] = {"observed_mean": round(float(df["pressure"].mean()), 4),
                       "observed_compliance": round(float(((df["pressure"] >= BAND[0]) & (df["pressure"] <= BAND[1])).mean()), 4),
                       "b3_surge": dict(Counter(df["b3_surge"]))}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
