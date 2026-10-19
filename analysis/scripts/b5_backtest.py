"""B5 正式回测: B1 规则 与 B3+B4 优化 的连续状态回放 (只读, 不改 B1-B4 算法)。

要点:
  - 按时间戳顺序、5 分钟采样; 逐步继承设备开停/流量、连续运行/停机时长、
    启停次数与动作锁定 (跨步传递 PolicyState);
  - B1 与 B3 各自独立维护模拟状态; 使用相同初始状态、预测、容量/健康、备用、
    压力、功率模型与最短启停限制; 不读取未来真实负荷;
  - 仅供模拟: 现场可确认状态一律 unknown; 阻断/未知/不可行不推进状态;
  - 电量 energy_kwh = Σ power_kw × step_minutes/60; 供气 m3 = Σ flow × step_minutes。

输出:
  analysis/reports/b/b5_ledger.csv
  analysis/reports/b/b5_backtest_metrics.json
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

from air_station.baseline import pipeline as PIPE
from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF
from air_station.optimization.service import load_optimization_config, optimize_dispatch

LEDGER = Path("analysis/reports/b/b5_ledger.csv")
METRICS = Path("analysis/reports/b/b5_backtest_metrics.json")
BAND = (0.55, 0.66)
RATED = 268.9
SINGLE_WITH_EXTRA = 268.9 / 1.05
BIG_SMALL_RATIO = 0.5


def _sim_snapshot(hist_snap, sim, as_of):
    """用模拟的开停/流量 + 历史测量的压力/IGV/BOV 构造快照。"""
    devs = []
    for d in hist_snap.devices:
        s = sim.get(d.device_id, {"running": d.running, "flow": d.flow_m3_min})
        devs.append(C.DeviceSnapshot(d.device_id, bool(s["running"]), s.get("flow"),
                                     None, d.discharge_pressure_mpa, d.igv_percent,
                                     d.bov_closed_percent, d.quality_valid, "replay_sim"))
    return C.StationSnapshot(as_of, hist_snap.header_pressure_mpa, devs, "replay_sim")


def executable(result) -> bool:
    """候选是否可作为模拟执行动作 (不推进状态的条件见各分支)。

    仅当 status=success、未被 B4 阻断、无过渡容量缺口、且过渡各步供气成立时可执行。
    """
    if result.status != "success" or result.safety_blocked:
        return False
    if C.RC_TRANSITION_CAPACITY_GAP in result.reason_codes:
        return False
    for s in (result.transition_plan or []):
        if not s.get("supply_ok", True):
            return False
    return True


def _apply(sim, result, tstr, state):
    for a in result.device_actions:
        sim[a.device_id] = {"running": a.running_after, "flow": a.target_flow_m3_min}
        if a.action in ("start", "stop"):
            state.last_switch_at[a.device_id] = tstr
            state.recent_switch_times.append(tstr)


def _dev_power(actions, model):
    out = {"4#": None, "5#": None}
    for a in actions:
        if a.running_after:
            r = PERF.estimate_power(a.device_id, a.target_flow_m3_min or 0.0, model_path=model)
            out[a.device_id] = r["estimated_power_kw"] if r["status"] == "success" else None
    return out


def _dev_flow(actions):
    return {a.device_id: (a.target_flow_m3_min or 0.0) if a.running_after else 0.0
            for a in actions}


def _frozen(sim):
    """未执行步的流量: 取当前冻结的模拟状态 (运行则为该流量, 否则 0)。"""
    return {d: (float(sim[d]["flow"] or 0.0) if sim[d]["running"] else 0.0) for d in sim}


def replay(step_minutes=5):
    base_cfg = B1.load_config(B1.DEFAULT_CONFIG)
    con = C.DispatchConstraints.from_config(base_cfg)
    oc = load_optimization_config()
    model = oc["paths"]["power_model"]
    device_df, station_df = PIPE.load_processed()
    forecasts = PIPE.load_frozen_forecasts(horizon_minutes=base_cfg["demand"]["horizon_minutes"])
    sample = sorted(forecasts)[::step_minutes]

    # 初始状态: 用首个采样时刻的历史开停/流量
    c0 = PIPE.snapshot_from_frames(device_df, station_df, sample[0])
    sim1 = {d.device_id: {"running": bool(d.running), "flow": d.flow_m3_min} for d in c0.devices}
    sim3 = json.loads(json.dumps(sim1))
    st1, st3 = C.PolicyState(), C.PolicyState()

    rows = []
    for t in sample:
        ts = str(t)
        hist = PIPE.snapshot_from_frames(device_df, station_df, t)
        f = forecasts[t]
        snap1 = _sim_snapshot(hist, sim1, ts)
        snap3 = _sim_snapshot(hist, sim3, ts)

        r1 = B1.decide(snap1, f, [], con, base_cfg, st1)
        r3 = optimize_dispatch(snap3, f, [], con, state=st3)
        b3_exec = executable(r3)
        if r1.status == "success":
            _apply(sim1, r1, ts, st1)
        if b3_exec:
            _apply(sim3, r3, ts, st3)

        p1 = _dev_power(r1.device_actions, model) if r1.status == "success" else {"4#": None, "5#": None}
        p3 = {"4#": None, "5#": None}
        if r3.status == "success":
            for a in r3.device_actions:
                if a.running_after:
                    p3[a.device_id] = a.estimated_power_kw
        # 未执行步: 流量记录为**冻结的模拟状态** (非 0), 避免伪启停; 状态不推进
        f1 = _dev_flow(r1.device_actions) if r1.status == "success" else _frozen(sim1)
        f3 = _dev_flow(r3.device_actions) if b3_exec else _frozen(sim3)

        st = station_df[station_df["time"] == t]
        row = {
            "t": ts, "required": f.demand_value(con.demand_basis),
            "b1_status": r1.status, "b1_reason": "|".join(r1.reason_codes),
            "b1_flow": r1.total_flow_m3_min if r1.status == "success" else None,
            "b1_power": (sum(x for x in p1.values() if x) if any(p1.values()) else None),
            "b1_p4": p1["4#"], "b1_p5": p1["5#"], "b1_f4": f1["4#"], "b1_f5": f1["5#"],
            "b1_n": sum(1 for x in f1.values() if x > 0),
            "b3_status": r3.status, "b3_reason": "|".join(r3.reason_codes),
            "b3_flow": r3.total_flow_m3_min if r3.status == "success" else None,
            "b3_power": r3.estimated_power_kw if r3.status == "success" else None,
            "b3_p4": p3["4#"], "b3_p5": p3["5#"], "b3_f4": f3["4#"], "b3_f5": f3["5#"],
            "b3_n": len(r3.target_combination),
            "b3_surge": r3.surge_status, "b3_blocked": bool(r3.safety_blocked), "b3_exec": bool(b3_exec),
            "obs_flow": float(st["total_flow_m3_min"].iloc[0]) if len(st) else None,
            "obs_power": float(st["total_power_kw"].iloc[0]) if len(st) else None,
            "obs_pressure": float(st["header_pressure_mpa"].iloc[0]) if len(st) else None,
        }
        rows.append(row)
    return pd.DataFrame(rows), {"step_minutes": step_minutes, "n_samples": len(sample),
                                "window": [str(sample[0]), str(sample[-1])]}


def _E(g, col, step):
    return float(g[col].dropna().sum()) * step / 60.0


def _V(g, col, step):
    return float(g[col].dropna().sum()) * step


def window_metrics(g, step, label):
    out = {"label": label, "n": int(len(g))}
    if not len(g):
        return out
    out["duration_h"] = round(len(g) * step / 60.0, 3)
    out["required_m3"] = round(_V(g, "required", step), 1)
    for pr in ("b1", "b3"):
        ok = g[f"{pr}_status"] == "success"
        if pr == "b3":
            ok = ok & g["b3_exec"]
        gg = g[ok]
        out[f"{pr}_evaluable_steps"] = int(len(gg))
        if len(gg):
            out[f"{pr}_energy_kwh"] = round(_E(gg, f"{pr}_power", step), 2)
            out[f"{pr}_supply_m3"] = round(_V(gg, f"{pr}_flow", step), 1)
            V = out[f"{pr}_supply_m3"]
            out[f"{pr}_sec"] = round(out[f"{pr}_energy_kwh"] / V, 6) if V else None
            out[f"{pr}_shortfall_m3"] = round(float((gg["required"] - gg[f"{pr}_flow"]).clip(lower=0).sum()) * step, 3)
    # 公平比较: 两策略均可执行步
    both = (g.b1_status == "success") & (g.b3_status == "success") & g.b3_exec
    if both.any():
        bb = g[both]
        E1, E3 = _E(bb, "b1_power", step), _E(bb, "b3_power", step)
        V1, V3 = _V(bb, "b1_flow", step), _V(bb, "b3_flow", step)
        out["fair_steps"] = int(len(bb))
        out["energy_reduction_pct"] = round((E1 - E3) / E1 * 100, 4) if E1 else None
        out["sec_reduction_pct"] = round((E1 / V1 - E3 / V3) / (E1 / V1) * 100, 4) if V1 and V3 else None
    # 风险与压力未知
    out["b3_surge"] = dict(Counter(g["b3_surge"]))
    out["b3_blocked_steps"] = int(g["b3_blocked"].sum())
    p = g["obs_pressure"].dropna()
    out["pressure_unknown_steps"] = int((p <= 0).sum() + g["obs_pressure"].isna().sum())
    out["obs_reference"] = {"n": int(g["obs_flow"].notna().sum()),
                            "obs_supply_m3": round(_V(g, "obs_flow", step), 1),
                            "obs_energy_kwh": round(_E(g, "obs_power", step), 2)}
    # 大马拉小车 (运行机组 load_ratio<0.5) 时长
    for pr in ("b1", "b3"):
        mins = 0.0
        for did in ("4#", "5#"):
            col = f"{pr}_f{did[-2]}"
            if col in g:
                mins += float(((g[col] > 0) & (g[col] / RATED < BIG_SMALL_RATIO)).sum()) * step
        out[f"{pr}_big_small_minutes"] = round(mins, 1)
    # 逐机启停次数 (运行开关状态变化)
    for pr in ("b1", "b3"):
        for did in ("4#", "5#"):
            col = f"{pr}_f{did[-2]}"
            if col in g:
                on = (g[col].fillna(0) > 0).astype(int)
                out[f"{pr}_{did}_switches"] = int((on.diff().abs() == 1).sum())
    return out


def main():
    step = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    t0 = time.perf_counter()
    df, meta = replay(step)
    df.to_csv(LEDGER, index=False) if step == 5 else None

    common = (df.b1_status == "success") & (df.b3_status == "success")
    retained = common & df.b3_exec
    matched = common & ((df.b1_flow - df.b3_flow).abs() <= 1.0)
    infeas = df.b1_status != "success"

    rep = {"meta": {**meta, "runtime_s": round(time.perf_counter() - t0, 1),
                    "stateful_replay": True,
                    "field_confirmable": "unknown (offline simulation; not field-confirmed)"}}
    rep["windows"] = {
        "all": window_metrics(df, step, "all"),
        "common_feasible_preB4": window_metrics(df[common], step, "common_feasible_preB4"),
        "b4_retained": window_metrics(df[retained], step, "b4_retained"),
        "matched_posthoc": window_metrics(df[matched], step, "matched_posthoc"),
    }
    rep["infeasible"] = {"n": int(infeas.sum()),
                         "required_m3_unevaluable": round(_V(df[infeas], "required", step), 1),
                         "note": "容量不可行; 无供气动作; 不可评估; 不含 B4 阻断步"}
    rep["blocked_subset_of_common"] = {"common_n": int(common.sum()),
                                        "blocked_n": int((common & df.b3_blocked).sum()),
                                        "note": "B4 阻断为共同可行子集, 不属容量不可行"}
    # 分档
    c = df[common].copy()
    if len(c):
        q = c["required"].quantile([1 / 3, 2 / 3]).tolist()
        c["level"] = c["required"].map(lambda x: "low" if x <= q[0] else ("mid" if x <= q[1] else "high"))
        rep["by_level"] = {lv: window_metrics(g, step, lv) for lv, g in c.groupby("level")}
    METRICS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2)[:4500])


if __name__ == "__main__":
    main()
