"""A6 驱动 B5 连续回测 (B1 / 冻结 B3+B4 / myopic / multi_step / DP v2)。

协议 (冻结):
  - 窗口: dev 2026-03-26~07-31, valid 2026-08-01~08-20; 步长 15 min, 发出网格对齐 A6 as_of。
  - 预测: A6 15/30/60 瞬时点; 需求口径 = **upper_bound** (CAL-02 冻结, 与 B1/B3 一致);
          need = required + extra 5%。
  - 规划器时域需求: [ub15, ub30, ub45(缺失回退), ub60]; 主口径 hold_previous(45->30),
    敏感性 next_available(45->60) 仅对 multi_step 在 valid 对照。
  - 瞬时点作为该步供气计划的工程近似 (非区间平均)。
  - 各策略 **同一预测、同一初始状态、相同设备约束与安全边界 (B4)**; 逐时刻继承开停/锁定/切换历史。
  - 评分真值 (A6 scoring) **仅事后评估**, 不进入优化决策。
  - 主对照 **不启用** 可选 RUL 惩罚; 健康/RUL 作为独立附加实验; 不使用 B5 最终窗口。
输出:
  analysis/reports/b/a6_b5_continuous.json
  analysis/reports/b/a6_b5_continuous_ledger.csv
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.forecast_adapter import load_forecast_bundle
from air_station.optimization.service import load_optimization_config, optimize_dispatch

ROOT = Path(__file__).resolve().parents[2]
HB = ROOT / "artifacts" / "handoff_b"
# A6BT_CAL=1 -> 使用 A6 校准预测(15/30/45/60/75) + 候选策略 cand; 输出另存, 不覆盖旧结果
CAL = os.environ.get("A6BT_CAL") == "1"
OUT = ROOT / ("analysis/reports/b/a6_b5_continuous_cal.json" if CAL else "analysis/reports/b/a6_b5_continuous.json")
LED = ROOT / ("analysis/reports/b/a6_b5_continuous_cal_ledger.csv" if CAL else "analysis/reports/b/a6_b5_continuous_ledger.csv")
W_LOW_CAND = 2.0   # 候选(DP v2) 低负荷惩罚权重 (开发段选定)

_spec = importlib.util.spec_from_file_location("ms_v1", ROOT / "analysis/scripts/multistep_optimization.py")
MS = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(MS)
_spec2 = importlib.util.spec_from_file_location("ms_v2", ROOT / "analysis/scripts/multistep_optimization_v2.py")
MS2 = importlib.util.module_from_spec(_spec2); _spec2.loader.exec_module(MS2)

DEVICE_IDS = ("4#", "5#")
STEP = 15
HORIZON = 4
RATED = 268.9
WINDOWS = {"dev": ("2026-03-26", "2026-07-31 23:45:00"),
           "valid": ("2026-08-01", "2026-08-20 23:40:00")}
STRATEGIES = (("b1_rule", "b3_b4", "myopic", "multi_step", "v2", "cand") if CAL
              else ("b1_rule", "b3_b4", "myopic", "multi_step", "v2"))
# 加载率目标 (来自 load_ratio_definition_v2)
TARGET_RATED = 1.0
TARGET_PLATEAU = {"4#": 0.8537, "5#": 0.9381}
TOL_PCT = 10.0


def _executable(r) -> bool:
    if r.status != "success" or r.safety_blocked:
        return False
    if C.RC_TRANSITION_CAPACITY_GAP in r.reason_codes:
        return False
    return all(s.get("supply_ok", True) for s in (r.transition_plan or []))


def _b3_power(r, model):
    tot = 0.0
    for a in r.device_actions:
        if a.running_after:
            e = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0, model_path=model)
            if e["status"] != "success":
                return None
            tot += e["estimated_power_kw"]
    return tot


def _ub(bundle, t, h):
    fr = bundle.forecast_result(t, h)
    return fr.upper_bound if fr else None


def _pf(bundle, t, h):
    fr = bundle.forecast_result(t, h)
    return fr.predicted_flow if fr else None


def run_window(name, bundle, device_df, station_df, oc, con, base_cfg, sensitivity=False):
    model = oc["paths"]["power_model"]
    power_cap = float(oc.get("dispatch", {}).get("power_cap_kw_per_device", 0.0) or 0.0)
    cache = {}

    def est(d, q):
        key = (d, round(float(q), 1))
        if key not in cache:
            r = PERF.estimate_power(d, float(q), model_path=model)
            cache[key] = r["estimated_power_kw"] if r["status"] == "success" else None
        return cache[key]

    lo, hi = WINDOWS[name]
    grid = [t for t in sorted(bundle.by_issue)
            if t.minute % STEP == 0 and lo <= str(t) <= hi and 15 in bundle.by_issue[t]]
    sim = {s: {} for s in STRATEGIES}
    stt = {s: C.PolicyState() for s in STRATEGIES}
    init = PIPE.snapshot_from_frames(device_df, station_df, grid[0])
    for s in STRATEGIES:
        sim[s] = {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in init.devices}

    rows = []
    for t in grid:
        ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        obs = bundle.actual_at_target(t, 15)
        ub = {h: _ub(bundle, t, h) for h in (15, 30, 45, 60)}
        pf = {h: _pf(bundle, t, h) for h in (15, 30, 45, 60)}
        if any(v is None for v in (ub[15], ub[30], ub[60])):
            continue
        ub45 = ub[45] if ub[45] is not None else ub[30]        # 45min 缺失 -> hold_previous(30)
        req_main = [ub[15], ub[30], ub45, ub[60]]
        lbs = {h: (bundle.forecast_result(t, h).lower_bound if bundle.forecast_result(t, h) else None)
               for h in (15, 30, 60)}
        row = {"t": ts, "obs_flow": (float(obs) if obs is not None else None),
               "pf15": pf[15], "lb15": lbs[15], "ub15": ub[15],
               "lb30": lbs[30], "ub30": ub[30], "lb60": lbs[60], "ub60": ub[60]}
        for s in STRATEGIES:
            snap = MS._sim_snapshot(hist, sim[s], ts)
            caps = CONS.build_capabilities(con, snap, [], stt[s])
            cur_on = {d for d in DEVICE_IDS if sim[s][d]["running"]}
            cur_flows = {d: sim[s][d]["flow"] for d in DEVICE_IDS}
            row[f"{s}_status"] = "infeasible"; row[f"{s}_flow"] = row[f"{s}_power"] = None
            row[f"{s}_blocked"] = row[f"{s}_exec"] = False; row[f"{s}_f4"] = row[f"{s}_f5"] = 0.0
            row[f"{s}_n"] = 0; row[f"{s}_switches"] = 0
            try:
                if s == "b1_rule":
                    fr = C.ForecastResult(ts, 15, pf[15], None, ub[15], "a6")
                    r = B1.decide(snap, fr, [], con, base_cfg, stt[s])
                    if r.status != "success":
                        row[f"{s}_status"] = r.status
                        continue
                    acts = r.device_actions
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in acts if a.running_after}
                    combo = tuple(flows.keys())
                    p = _b3_power(r, model)
                    if p is None:
                        continue
                    trans = r.transition_plan or []
                elif s == "b3_b4":
                    fr = C.ForecastResult(ts, 15, pf[15], None, ub[15], "a6")
                    r = optimize_dispatch(snap, fr, [], con, state=stt[s])
                    if r.status != "success":
                        row[f"{s}_status"] = r.status; continue
                    flows = {a.device_id: (a.target_flow_m3_min or 0.0) for a in r.device_actions if a.running_after}
                    combo = tuple(flows.keys())
                    acts = r.device_actions; p = r.estimated_power_kw
                    trans = r.transition_plan or []
                else:
                    if s == "v2":
                        b = MS2.plan_v2(req_main, caps, con, est, cur_on, stt[s].last_switch_at, ts,
                                        power_cap, 0.0, MS.SWITCH_PENALTY_KWH)
                    elif s == "cand":
                        b = MS2.plan_v2(req_main, caps, con, est, cur_on, stt[s].last_switch_at, ts,
                                        power_cap, W_LOW_CAND, MS.SWITCH_PENALTY_KWH)
                    else:
                        req = req_main[:1] if s == "myopic" else req_main
                        b = MS.plan(req, caps, con, model, cur_on, stt[s].last_switch_at, ts, power_cap)
                    if b is None:
                        continue
                    combo = b["seq"][0]
                    al = b["alloc0"] if s in ("multi_step", "myopic") else b["alloc0"]
                    acts = MS._actions(combo, al, caps, cur_on, cur_flows)
                    flows = al["flows"]; p = al["power_kw"]; trans = b.get("seq") and \
                        CONS.transition_feasibility(cur_on, combo, caps, req_main[0], con.reserve)[1] or []
                surge, blocked, _ = MS._surge(snap, acts, trans)
                trans_ok = all(x.get("supply_ok", True) for x in trans)
                exec_ok = (not blocked) and trans_ok
                row[f"{s}_status"] = "success"
                row[f"{s}_flow"] = float(sum(flows.values()))
                row[f"{s}_power"] = float(p)
                row[f"{s}_f4"] = float(flows.get("4#", 0.0)); row[f"{s}_f5"] = float(flows.get("5#", 0.0))
                row[f"{s}_n"] = len(combo); row[f"{s}_blocked"] = blocked; row[f"{s}_exec"] = exec_ok
                if exec_ok:
                    MS._apply(sim[s], acts, ts, stt[s])
            except Exception as e:  # noqa
                row[f"{s}_status"] = f"error:{type(e).__name__}"
        rows.append(row)
    df = pd.DataFrame(rows)
    return df


def _low_min(g, s, step=STEP):
    tot = 0.0
    for d in ("4#", "5#"):
        col = f"{s}_f{d[0]}"
        if col in g:
            tot += float(((g[col] > 0) & (g[col] / RATED < 0.5)).sum()) * step
    return round(tot, 1)


def _switches(g, s, step=STEP):
    tot = 0
    for d in ("4#", "5#"):
        col = f"{s}_f{d[0]}"
        on = (g[col].fillna(0) > 0).astype(int)
        tot += int((on.diff().abs() == 1).sum())
    return tot


def _dev_stats(g, s, target):
    """加载率偏离: 逐机 mean|d|%, 超限比例 (|d|>10%)。"""
    vals, ex, tot = [], 0.0, 0
    for d in ("4#", "5#"):
        f = g[f"{s}_f{d[0]}"].to_numpy(float)
        lam = f / RATED
        m = f > 0
        if not m.any():
            continue
        t = target[d] if isinstance(target, dict) else target
        d_ = (lam[m] - t) / t * 100.0
        vals.append(float(np.mean(np.abs(d_)))); ex += float((np.abs(d_) > TOL_PCT).sum()); tot += int(m.sum())
    return {"dev_absmean_pct": round(float(np.mean(vals)), 3) if vals else None,
            "exceed_ratio": round(ex / tot, 4) if tot else None}


def metrics(df, name):
    dt_h = STEP / 60.0
    rep = {"protocol_window": name, "n_steps": int(len(df))}
    # A6 预测/区间质量 (per horizon; 仅事后, 用 scoring 真值)
    sub = df.dropna(subset=["obs_flow"])
    act = sub["obs_flow"].to_numpy()
    q = {}
    for h in (15, 30, 60):
        pred = sub[f"ub{h}"].to_numpy(); lb = sub[f"lb{h}"].to_numpy()
        e = pred - act
        q[f"{h}min"] = {"n": int(len(act)),
                        "MAE_vs_actual": round(float(np.mean(np.abs(e))), 4) if len(act) else None,
                        "MAPE_floor1": round(float(np.mean(np.abs(e) / np.maximum(act, 1.0)) * 100), 3) if len(act) else None,
                        "WAPE": round(float(np.sum(np.abs(e)) / np.sum(act) * 100), 3) if act.sum() else None,
                        "coverage_90": round(float(np.mean((act >= lb) & (act <= pred))), 4) if len(act) else None,
                        "underestimate_rate": round(float(np.mean(act > pred)), 4) if len(act) else None}
    rep["a6_quality_vs_actual"] = q
    # 区间违反 vs 缺供对应 (步0)
    if len(sub):
        viol = (act > sub["ub15"].to_numpy()).astype(int)
        short = {}
        for s in STRATEGIES:
            sh = (sub["obs_flow"].fillna(0).to_numpy() - sub[f"{s}_flow"].fillna(0).to_numpy()).clip(min=0)
            both = int(((viol == 1) & (sh > 0)).sum())
            v_only = int(((viol == 1) & (sh <= 0)).sum())
            s_only = int(((viol == 0) & (sh > 0)).sum())
            short[s] = {"viol_and_shortfall": both, "viol_no_shortfall": v_only, "shortfall_no_viol": s_only}
        rep["interval_violation_vs_shortfall"] = short
    rep["per_strategy"] = {}
    for s in STRATEGIES:
        g_all = df[df[f"{s}_status"] == "success"]
        g = g_all[g_all[f"{s}_exec"]]
        n = len(g)
        E = float(g[f"{s}_power"].sum()) * dt_h if n else 0.0
        V = float(g[f"{s}_flow"].sum()) * STEP if n else 0.0
        short = float((g["obs_flow"].fillna(0) - g[f"{s}_flow"]).clip(lower=0).sum()) * STEP if n else 0.0
        rep["per_strategy"][s] = {
            "success_steps": int(len(g_all)), "executable_steps": n,
            "exec_rate": round(n / len(df), 4) if len(df) else None,
            "energy_kwh": round(E, 2), "supply_m3": round(V, 1),
            "shortfall_vs_actual_m3": round(short, 2),
            "sec": round(E / V, 6) if V else None,
            "big_small_minutes": _low_min(g, s), "big_small_f4": _low_min_device(g, s, "4#"),
            "big_small_f5": _low_min_device(g, s, "5#"),
            "switches": _switches(g, s),
            "load_ratio_rated": _dev_stats(g, s, TARGET_RATED),
            "load_ratio_plateau": _dev_stats(g, s, TARGET_PLATEAU),
        }
    # 三统计范围
    for scope in ("full", "common", "service"):
        if scope == "full":
            g = df
        elif scope == "common":
            ok = df[[f"{s}_status" for s in STRATEGIES]].eq("success").all(axis=1)
            for s in STRATEGIES:
                ok = ok & df[f"{s}_exec"]
            g = df[ok]
        else:
            ok = df[[f"{s}_status" for s in STRATEGIES]].eq("success").all(axis=1)
            for s in STRATEGIES:
                ok = ok & df[f"{s}_exec"]
            g = df[ok]
            for s in STRATEGIES:
                g = g[g[f"{s}_flow"] >= g["obs_flow"].fillna(-1) - 0.05]
        blk = {"n_steps": int(len(g))}
        if len(g):
            E = {s: float(g[f"{s}_power"].sum()) * dt_h for s in STRATEGIES}
            blk["energy_kwh"] = {s: round(E[s], 2) for s in STRATEGIES}
            b1 = E["b1_rule"]
            blk["energy_saving_vs_b1_pct"] = {s: (round((b1 - E[s]) / b1 * 100, 4) if b1 else None) for s in STRATEGIES}
            bs = {s: _low_min(g, s) for s in STRATEGIES}
            blk["big_small_minutes"] = bs
            blk["big_small_reduction_vs_b1_pct"] = {s: (round((bs["b1_rule"] - bs[s]) / bs["b1_rule"] * 100, 4)
                                                        if bs["b1_rule"] > 0 else "n/a") for s in STRATEGIES}
            blk["supply_m3"] = {s: round(float(g[f"{s}_flow"].sum()) * STEP, 1) for s in STRATEGIES}
            blk["shortfall_vs_actual_m3"] = {s: round(float((g["obs_flow"].fillna(0) - g[f"{s}_flow"]).clip(lower=0).sum()) * STEP, 2) for s in STRATEGIES}
        rep[f"scope_{scope}"] = blk
    return rep


def _low_min_device(g, s, did):
    col = f"{s}_f{did[0]}"
    return round(float(((g[col] > 0) & (g[col] / RATED < 0.5)).sum()) * STEP, 1) if col in g else 0.0


def main():
    t0 = time.perf_counter()
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    device_df, station_df = PIPE.load_processed()
    scor = pd.read_parquet(HB / ("forecast_scoring_actuals_calibrated.parquet" if CAL
                                  else "forecast_scoring_actuals.parquet"))
    rep = {"protocol": {
        "windows": WINDOWS, "step_minutes": STEP, "horizon_steps": HORIZON,
        "demand_basis": "upper_bound (CAL-02 frozen)", "reserve": "extra 5%",
        "planner_horizon_demand": "['ub15','ub30','ub45->ub30 (hold_previous)','ub60']",
        "point_vs_interval": "A6 瞬时点作为该步供气计划工程近似; 非区间平均",
        "state": "各策略独立继承开停/锁定/切换 (同一初态/约束/B4)",
        "truth": "A6 scoring actual 仅事后评估; 不进入决策",
        "rul_penalty": "主对照不启用; 健康/RUL 另作附加实验",
        "uses_B5_final_window": False,
        "evidence": "开发/验证窗口; 验证段重复使用, 非独立最终测试"}}
    ledgers = {}
    suffix = "_calibrated" if CAL else ""
    for name in ("dev", "valid"):
        b = load_forecast_bundle(
            HB / f"forecast_{'dev' if name == 'dev' else 'validation'}_dispatch{suffix}.parquet",
            scoring=scor, scoring_split=("dev" if name == "dev" else "valid"))
        df = run_window(name, b, device_df, station_df, oc, con, base_cfg)
        ledgers[name] = df
        rep[name] = metrics(df, name)
    # 合并账本
    for name, df in ledgers.items():
        df.insert(0, "window", name)
    pd.concat(ledgers.values(), ignore_index=True).to_csv(LED, index=False)
    rep["runtime_s"] = round(time.perf_counter() - t0, 1)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: rep[k]["scope_common"] for k in ("dev", "valid")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
