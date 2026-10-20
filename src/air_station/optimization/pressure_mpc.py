"""Pressure-Aware MPC (独立新模块; 不修改冻结 B1-B4 / UC / DP v2 / cand)。

四步滚动时域内联合优化 机组开停 + 供气量 + 压力状态:
  - 需求: A6 校准 15/30/45/60 预测的 predicted_flow(期望) 与 upper_bound(上界);
  - 供气水平候选: 每步 {pred, upper} 两级 —— 在服务下限(pred)与上界(upper)之间按压力反馈选择;
  - 压力动态: 独立一阶储气模型 (simulation 假设) p_{k+1}=p_k+dt*((sup-dem)/K_VOL - LEAK*(p_k-P_ENV));
  - 约束: extra 备用、容量、最低稳定流量、爬坡、最短开停、过渡供气、B4;
  - 目标: 先安全/服务(缺供重罚、压力越界重罚、终止压力不降低), 再能耗, 再低负荷, 最后切换;
  - 不可行 -> 返回 None (调用方安全回退)。

防"虚假节能": terminal_pressure_non_decreasing 保证四步内**不净消耗储气**; 供气不得低于 pred。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF

DEVICE_IDS = ("4#", "5#")
RATED = 268.9
LOW_LIMIT = 0.5
DEFAULT_CONFIG = "configs/pressure_mpc.yaml"


@dataclass
class MPCDyn:
    k_vol: float = 23700.0
    leak: float = 0.001
    p_env: float = 0.55
    p_min: float = 0.40
    p_max: float = 0.75
    p_set: float = 0.60


@dataclass
class MPCConfig:
    band: tuple[float, float] = (0.55, 0.66)
    dyn: MPCDyn = field(default_factory=MPCDyn)
    horizon: int = 4
    step_minutes: int = 15
    demand_basis: str = "pred"
    levels: tuple[str, ...] = ("pred", "upper")
    terminal_non_decreasing: bool = True
    p_end_drop: float = 0.005
    w_energy: float = 1.0
    w_low: float = 1.0
    w_switch: float = 2.0
    w_pressure: float = 5000.0
    w_short: float = 1.0e6
    ramp: dict = field(default_factory=lambda: {"4#": 77.0, "5#": 287.0})


def load_mpc_config(path: str | Path = DEFAULT_CONFIG) -> MPCConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[pressure_mpc] 配置缺失: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    pr = raw.get("pressure", {}); mpc = raw.get("mpc", {}); dyn = pr.get("dynamics", {})
    w = mpc.get("weights", {})
    return MPCConfig(
        band=tuple(pr.get("band_mpa", [0.55, 0.66])),
        dyn=MPCDyn(k_vol=float(dyn.get("K_VOL_m3_per_mpa", 23700.0)), leak=float(dyn.get("leak_per_min", 0.001)),
                   p_env=float(dyn.get("p_env_mpa", 0.55)), p_min=float(dyn.get("p_min_clip_mpa", 0.40)),
                   p_max=float(dyn.get("p_max_clip_mpa", 0.75)), p_set=float(dyn.get("p_setpoint_mpa", 0.60))),
        horizon=int(mpc.get("horizon_steps", 4)), step_minutes=int(mpc.get("step_minutes", 15)),
        demand_basis=str(mpc.get("demand_basis", "pred")), levels=tuple(mpc.get("supply_levels", ["pred", "upper"])),
        terminal_non_decreasing=bool(mpc.get("terminal_pressure_non_decreasing", True)),
        p_end_drop=float(mpc.get("allowed_p_end_drop_mpa", 0.005)),
        w_energy=float(w.get("energy_kwh", 1.0)), w_low=float(w.get("low_load_step", 1.0)),
        w_switch=float(w.get("switch_event", 2.0)), w_pressure=float(w.get("pressure_violation", 5000.0)),
        w_short=float(w.get("shortfall_m3min", 1.0e6)),
        ramp=dict(mpc.get("ramp", {"4#": 77.0, "5#": 287.0})))


def _power(d, f, model, cache):
    key = (d, round(float(f), 1))
    if cache is not None and key in cache:
        return cache[key]
    r = PERF.estimate_power(d, float(f), model_path=model)
    v = r["estimated_power_kw"] if r["status"] == "success" else None
    if cache is not None:
        cache[key] = v
    return v


def _alloc_min_power(combo, required, caps, prev_flows, prev_running, model, cfg, cache):
    """组合内最小功率分配 (满足 required, 受 min/max+爬坡); 返回 flows/power/low/shortfall。"""
    if not combo:
        return {"flows": {}, "power_kw": 0.0, "device_power": {}, "low": False, "shortfall": max(0.0, required)}
    bnd = {}
    for d in combo:
        lo, hi = caps[d].min_flow_m3min, caps[d].max_flow_m3min
        if prev_running.get(d, False) and prev_flows.get(d) is not None:
            rm = cfg.ramp[d]
            lo = max(lo, prev_flows[d] - rm); hi = min(hi, prev_flows[d] + rm)
        bnd[d] = (lo, max(lo, hi))
    import numpy as np
    best = None
    if len(combo) == 1:
        d = combo[0]
        f = min(max(required, bnd[d][0]), bnd[d][1])
        p = _power(d, f, model, cache)
        if p is None:
            return None
        return {"flows": {d: f}, "power_kw": p, "device_power": {d: p},
                "low": (f / RATED) < LOW_LIMIT, "shortfall": max(0.0, required - f)}
    d1, d2 = combo
    lo_sum = bnd[d1][0] + bnd[d2][0]; hi_sum = bnd[d1][1] + bnd[d2][1]
    tot0 = max(required, lo_sum)
    for tot in np.linspace(tot0, hi_sum, 5):
        extra = tot - lo_sum
        for r in np.linspace(0.0, 1.0, 21):
            f1 = bnd[d1][0] + min(extra * r, bnd[d1][1] - bnd[d1][0])
            f2 = tot - f1
            if f2 < bnd[d2][0] - 1e-6 or f2 > bnd[d2][1] + 1e-6:
                continue
            p1 = _power(d1, f1, model, cache); p2 = _power(d2, f2, model, cache)
            if p1 is None or p2 is None:
                continue
            cand = {"flows": {d1: f1, d2: f2}, "power_kw": p1 + p2, "device_power": {d1: p1, d2: p2},
                    "low": (f1 / RATED < LOW_LIMIT) or (f2 / RATED < LOW_LIMIT), "shortfall": max(0.0, required - tot)}
            if best is None or cand["power_kw"] < best["power_kw"] - 1e-9:
                best = cand
    return best


def _switch_events(prev, combo):
    return [d for d in DEVICE_IDS if (d in combo) != (d in prev)]


def plan_mpc(pred_seq, ub_seq, caps, con, cur_on, cur_flows, last_switch, as_of, p_start,
             model_path="artifacts/models/power_model.json", power_cap=0.0,
             cfg: MPCConfig = None, cache=None) -> Optional[dict]:
    cfg = cfg or MPCConfig()
    H = len(pred_seq)
    dt = cfg.step_minutes
    dt_h = dt / 60.0
    import itertools

    # 每步每组合的两级分配 (pred / upper)
    step_opts = []
    for k in range(H):
        opts = {}
        for combo in CONS.COMBINATIONS:
            if any(not caps[d].available for d in combo):
                continue
            need = ub_seq[k] + con.reserve.extra_m3_min(ub_seq[k])
            cap = sum(caps[d].max_flow_m3min for d in combo)
            if combo and cap + 1e-6 < need:
                continue
            if not combo and pred_seq[k] > 1e-6:
                continue
            pf = cur_flows if k == 0 else {d: caps[d].min_flow_m3min for d in DEVICE_IDS}
            pr = {d: (d in cur_on) if k == 0 else True for d in DEVICE_IDS}
            lvl_opts = {}
            for lvl in cfg.levels:
                req = pred_seq[k] if lvl == "pred" else ub_seq[k]
                al = _alloc_min_power(combo, req, caps, pf, pr, model_path, cfg, cache)
                if al is None:
                    continue
                if power_cap > 0 and any(_power(d, al["flows"][d], model_path, cache) > power_cap + 1e-6
                                         for d in combo):
                    continue
                lvl_opts[lvl] = al
            if lvl_opts:
                opts[combo] = lvl_opts
        step_opts.append(opts)
    if any(len(o) == 0 for o in step_opts):
        return None

    lo_band, hi_band = cfg.band
    best = None
    for seq in itertools.product(*[[(c, l) for c in o for l in o[c]] for o in step_opts]):
        combos = tuple(c for c, _ in seq)
        if not _can_switch(cur_on, combos[0], as_of, last_switch, con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, combos[0], caps, ub_seq[0], con.reserve)
        if not ok:
            continue
        prev, sw, low, energy, short, pviol = set(cur_on), 0, 0, 0.0, 0.0, 0.0
        p = float(p_start)
        for k, (combo, lvl) in enumerate(seq):
            sw += len(_switch_events(prev, combo)); prev = set(combo)
            al = step_opts[k][combo][lvl]
            energy += al["power_kw"] * dt_h
            low += 1 if al["low"] else 0
            short += al["shortfall"]
            sup = sum(al["flows"].values())
            p = p + dt * ((sup - pred_seq[k]) / cfg.dyn.k_vol - cfg.dyn.leak * (p - cfg.dyn.p_env))
            p = min(max(p, cfg.dyn.p_min), cfg.dyn.p_max)
            if p < lo_band:
                pviol += (lo_band - p)
            elif p > hi_band:
                pviol += (p - hi_band)
        if sw > con.max_switches_per_hour:
            continue
        if cfg.terminal_non_decreasing and p < float(p_start) - cfg.p_end_drop:
            continue
        cost = (cfg.w_energy * energy + cfg.w_low * low + cfg.w_switch * sw
                + cfg.w_pressure * pviol + cfg.w_short * short)
        key = (round(cost, 6), sw, combos)
        if best is None or key < best[0]:
            best = (key, {"seq": [(c, l) for c, l in seq], "switches": sw, "low_steps": low,
                          "energy_kwh": round(energy, 4), "shortfall_m3min": round(short, 4),
                          "pressure_violation": round(pviol, 5), "p_end": round(p, 4),
                          "alloc0": step_opts[0][seq[0][0]][seq[0][1]]})
    return best[1] if best else None


def _can_switch(cur_on, combo, as_of, last_switch, con):
    t = pd.Timestamp(as_of)
    for d in DEVICE_IDS:
        b, a = d in cur_on, d in combo
        if b == a:
            continue
        last = last_switch.get(d)
        if last is None:
            continue
        delta = (t - pd.Timestamp(last)).total_seconds() / 60.0
        if a and delta < con.min_stop_minutes:
            return False
        if (not a) and delta < con.min_run_minutes:
            return False
    return True


def pressure_step(p, supply, demand, cfg: MPCConfig) -> float:
    """单步压力推进 (供参考/仿真)。"""
    dt = cfg.step_minutes
    p = p + dt * ((supply - demand) / cfg.dyn.k_vol - cfg.dyn.leak * (p - cfg.dyn.p_env))
    return min(max(p, cfg.dyn.p_min), cfg.dyn.p_max)
