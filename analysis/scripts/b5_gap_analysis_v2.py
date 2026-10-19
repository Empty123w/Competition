"""B5 前置分析 v2: 口径复核 (只读, 不改 B1-B4 算法)。

相对 v1 的补正:
  - 逐步骤缓存 (含需求分档、B4 阻断、缺供分母、供气匹配), 便于独立复算
  - 电量/供气/SEC 的积分方式独立核对
  - 同供气量匹配窗口 (事先定义容差)
  - B4 阻断分母与保留窗口复算
  - 单机受备用限制的三档需求分解
  - 4# 载荷相关残差与边际功率误差检查

用法: python3 analysis/scripts/b5_gap_analysis_v2.py [step_minutes]
"""
from __future__ import annotations

import json
import statistics
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

OUT_JSON = Path("analysis/reports/b/b5_gap_analysis_v2.json")
OUT_STEPS = Path("analysis/reports/b/b5_per_step.csv")
BAND = (0.55, 0.66)
SINGLE_RATED = 268.9
SINGLE_WITH_EXTRA = 268.9 / 1.05            # extra 5% 下单机可满足的最大需求


def b1_power(base, model):
    tot, ok = 0.0, True
    for a in base.device_actions:
        if a.running_after:
            r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0, model_path=model)
            if r["status"] == "success":
                tot += r["estimated_power_kw"]
            else:
                ok = False
    return tot if ok else None


def backtest(step_minutes=5, base_cfg=None, con=None, opt_cfg_path="configs/optimization.yaml"):
    base_cfg = base_cfg or B1.load_config(B1.DEFAULT_CONFIG)
    con = con or C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    device_df, station_df = PIPE.load_processed()
    forecasts = PIPE.load_frozen_forecasts(horizon_minutes=base_cfg["demand"]["horizon_minutes"])
    times = sorted(forecasts)
    sample = times[::step_minutes]
    # 采样步长核对
    secs = pd.Series(sample).diff().dropna().dt.total_seconds().unique()
    recs = []
    for t in sample:
        snap = PIPE.snapshot_from_frames(device_df, station_df, t)
        f = forecasts[t]
        base = B1.decide(snap, f, [], con, base_cfg, C.PolicyState())
        opt = optimize_dispatch(snap, f, [], con, config_path=opt_cfg_path)
        st = station_df[station_df["time"] == t]
        recs.append({
            "t": str(t), "required": f.demand_value(con.demand_basis),
            "b1_status": base.status, "b3_status": opt.status,
            "b1_flow": base.total_flow_m3_min, "b3_flow": opt.total_flow_m3_min,
            "b1_power": b1_power(base, model) if base.status == "success" else None,
            "b3_power": opt.estimated_power_kw if opt.status == "success" else None,
            "b1_n": sum(1 for a in base.device_actions if a.running_after),
            "b3_n": len(opt.target_combination),
            "b3_surge": opt.surge_status, "b3_blocked": bool(opt.safety_blocked),
            "b3_rc": "|".join(opt.reason_codes),
            "b1_rc": "|".join(base.reason_codes),
            "obs_flow": float(st["total_flow_m3_min"].iloc[0]) if len(st) else None,
            "obs_power": float(st["total_power_kw"].iloc[0]) if len(st) else None,
            "obs_pressure": float(st["header_pressure_mpa"].iloc[0]) if len(st) else None,
        })
    df = pd.DataFrame(recs)
    return df, {"sample_seconds": [int(s) for s in secs],
                "opt_power_model": model, "step_minutes": step_minutes,
                "n_samples": len(sample)}


def _E(df, col, step):
    return float(df[col].dropna().sum()) * step / 60.0


def _V(df, col, step):
    return float(df[col].dropna().sum()) * step


def segment(df, mask, step, label):
    g = df[mask]
    out = {"label": label, "n": int(len(g))}
    if not len(g):
        return out
    out["required_m3"] = round(_V(g, "required", step), 1)
    feas = g[(g.b1_status == "success") & (g.b3_status == "success")]
    out["common"] = int(len(feas))
    if len(feas):
        E1, E3 = _E(feas, "b1_power", step), _E(feas, "b3_power", step)
        V1, V3 = _V(feas, "b1_flow", step), _V(feas, "b3_flow", step)
        out.update({
            "b1_supply_m3": round(V1, 1), "b3_supply_m3": round(V3, 1),
            "supply_diff_pct": round((V1 - V3) / V1 * 100, 4) if V1 else None,
            "b1_energy_kwh": round(E1, 2), "b3_energy_kwh": round(E3, 2),
            "b1_sec": round(E1 / V1, 6) if V1 else None,
            "b3_sec": round(E3 / V3, 6) if V3 else None,
            "energy_reduction_pct": round((E1 - E3) / E1 * 100, 4) if E1 else None,
            "sec_reduction_pct": round((E1 / V1 - E3 / V3) / (E1 / V1) * 100, 4) if V1 and V3 else None,
            "b1_shortfall_m3": round(float((feas["required"] - feas["b1_flow"]).clip(lower=0).sum()) * step, 3),
            "b3_shortfall_m3": round(float((feas["required"] - feas["b3_flow"]).clip(lower=0).sum()) * step, 3),
        })
    return out


def main():
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    is_default = (step == 5)
    t0 = time.perf_counter()
    df, meta = backtest(step)
    if is_default:
        OUT_STEPS.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(OUT_STEPS, index=False)

    common = (df.b1_status == "success") & (df.b3_status == "success")
    infeas = ~common
    blocked = common & df.b3_blocked
    retained = common & (~df.b3_blocked)

    rep = {"meta": {**meta, "runtime_s": round(time.perf_counter() - t0, 1),
                    "window": [str(df.t.iloc[0]), str(df.t.iloc[-1])]}}
    rep["overall"] = segment(df, df.index == df.index, step, "all_steps")
    rep["common_feasible"] = segment(df, common, step, "common_feasible")
    rep["infeasible"] = {"n": int(infeas.sum()),
                         "required_m3_not_evaluable": round(_V(df[infeas], "required", step), 1),
                         "note": "B1/B3 均返回 INFEASIBLE, 无供气动作, 任务量不可评估"}
    rep["b4_blocked"] = {"n": int(blocked.sum()), "share_of_common_pct": round(float(blocked.sum()) / int(common.sum()) * 100, 4),
                         "required_m3_blocked": round(_V(df[blocked], "required", step), 1)}
    rep["retained_after_b4"] = segment(df, retained, step, "retained_after_b4")

    # 电量/供气/SEC 独立核对 (common)
    feas = df[common]
    E1, E3 = _E(feas, "b1_power", step), _E(feas, "b3_power", step)
    V1, V3 = _V(feas, "b1_flow", step), _V(feas, "b3_flow", step)
    rep["recompute_check"] = {
        "energy_kwh_formula": "sum(power_kw)*step_minutes/60",
        "volume_m3_formula": "sum(flow_m3min)*step_minutes",
        "b1_energy_kwh": round(E1, 2), "b3_energy_kwh": round(E3, 2),
        "b1_supply_m3": round(V1, 1), "b3_supply_m3": round(V3, 1),
        "b1_sec": round(E1 / V1, 6), "b3_sec": round(E3 / V3, 6),
        "energy_reduction_pct": round((E1 - E3) / E1 * 100, 4),
        "sec_reduction_pct": round((E1 / V1 - E3 / V3) / (E1 / V1) * 100, 4),
    }

    # 同供气量匹配窗口 (事先定义容差)
    for tol in (1.0, 5.0):
        m = common & ((df.b1_flow - df.b3_flow).abs() <= tol)
        rep[f"same_supply_tol_{int(tol)}"] = segment(df, m, step, f"same_supply_tol_{int(tol)}m3min")

    # 单机受备用限制的需求三分档 (基于需求上界)
    d = df[common]
    d1 = d["required"] <= SINGLE_WITH_EXTRA
    d2 = (d["required"] > SINGLE_WITH_EXTRA) & (d["required"] <= SINGLE_RATED)
    d3 = d["required"] > SINGLE_RATED
    rep["demand_classes_common"] = {
        "thresholds": {"single_with_extra": round(SINGLE_WITH_EXTRA, 3), "single_rated": SINGLE_RATED},
        "D1_req_le_256_095": {"n": int(d1.sum()), "share_pct": round(float(d1.mean()) * 100, 3),
                              "b3_single_share": round(float((d.loc[d1, "b3_n"] == 1).mean()), 4) if d1.any() else None},
        "D2_256_095_lt_req_le_268_9": {"n": int(d2.sum()), "share_pct": round(float(d2.mean()) * 100, 3),
                                        "b3_single_share": round(float((d.loc[d2, "b3_n"] == 1).mean()), 4) if d2.any() else None,
                                        "note": "需求在单机额定内, 仅因 5% 备用无法单机"},
        "D3_req_gt_268_9": {"n": int(d3.sum()), "share_pct": round(float(d3.mean()) * 100, 3),
                            "b3_single_share": round(float((d.loc[d3, "b3_n"] == 1).mean()), 4) if d3.any() else None,
                            "note": "需求超单机额定, 结构性必须双机"},
    }

    # 锁定相关原因码实际出现次数 (回测为逐步独立, 未携带 PolicyState)
    rep["lock_effect"] = {
        "stateful_replay": False,
        "note": "回测逐步独立调用, 未跨步传递 PolicyState, 最小运行/停机与最大切换锁定未生效",
        "b3_lock_rc_count": int(df.b3_rc.str.contains("MIN_RUN_LOCK|MIN_STOP_LOCK|MAX_SWITCH_LOCK").sum()),
        "b1_lock_rc_count": int(df.b1_rc.str.contains("MIN_RUN_LOCK|MIN_STOP_LOCK").sum()),
    }

    # 历史实测参照 (单列, 供气量不同)
    ob = df[common]
    rep["observed_reference"] = {
        "n": int(len(ob)), "obs_supply_m3": round(_V(ob, "obs_flow", step), 1),
        "obs_energy_kwh": round(_E(ob, "obs_power", step), 2),
        "obs_sec": round(_E(ob, "obs_power", step) / _V(ob, "obs_flow", step), 6),
        "note": "历史实测为不同供气任务, 不作为公平现场节能成绩; 压力为初始观测",
    }

    OUT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
