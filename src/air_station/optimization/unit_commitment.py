"""新独立候选: 机组组合 + 负荷分配联合优化器 (unit commitment, UC)。

独立于 B3 / DP v2 / cand; 不修改冻结 B1-B4。
方法: 两机组合(4 种)枚举 + 组合内**连续分配**最优 (最小功率满足需求),
      时域 4 步再做序列枚举 (4^4) 与代价择优。

优先级 (服务与安全优先):
  1) 硬约束: 容量 + extra 备用、设备可用、最短开停机、过渡供气连续 (B4 安全不变);
     若无可行组合 -> 明确不可行, 安全回退(保持当前组合), 记录原因。
  2) 在可行候选中: 先最小化**不可避免缺供** (重罚), 再最小化能耗, 再最小化低负荷步, 最后切换。
     代价 = W_SHORT*shortfall_m3 + energy_kwh + W_LOW*low_steps + W_SWITCH*switches。

爬坡约束: 运行机组相邻步流量变化 <= ramp_step (数据驱动 p99 x STEP; 工程假设, 标注 simulation)。

需求口径: 支持 predicted / upper / mid / pred+margin (鲁棒需求); 真实流量仅事后评估。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from air_station.optimization import constraints as CONS
from air_station.optimization import contracts as C
from air_station.optimization import performance as PERF

DEVICE_IDS = ("4#", "5#")
COMBOS = CONS.COMBINATIONS
RATED = 268.9
LOW_LIMIT = 0.5
# 数据驱动爬坡上限 (m3/min per 15min step): p99(|Δflow per min|) x 15
RAMP_STEP = {"4#": 77.0, "5#": 287.0}
W_SHORT = 1.0e6      # 缺供重罚 (服务优先)
W_LOW = 0.0          # 低负荷步权重 (外部可设)
W_SWITCH = 2.0       # 切换等效能耗 (kWh)
GRID = 0.02          # 分配枚举分辨率


@dataclass
class UCConfig:
    w_low: float = 0.0
    w_short: float = 1.0e6
    w_switch: float = 2.0
    ramp_enabled: bool = True
    demand_basis: str = "upper"   # upper | pred | mid | pred_plus
    margin_m3min: float = 0.0     # pred_plus 裕量
    grid: float = GRID
    balanced: bool = False        # 均衡分配: 需求允许时, 每台运行机 >= lb_frac*RATED (消除可再平衡低负荷)
    demand_k: float = 1.0         # 需求在 [pred, upper] 间的位置: demand=pred+k*(upper-pred); k=1 -> upper(基线)
    lb_frac: float = 0.5          # 均衡下界比例 (0.5 -> 134.45, 脱离低负荷阈值)
    spread_balance: bool = False  # 机组间负荷均衡: 双机时尽量等分 (最小化 |λ4-λ5|)
    spread_tol: float = 0.0       # 允许的机组间加载率差 (比例); >0 时仅约束 |λ4-λ5|<=tol, 其余按最小功率


def demand_scaled(pred, upper, k):
    """校准区间置信度自适应需求: k=1 用 upper(保守), k=0 用 pred; 中间为收缩后需求。"""
    return float(pred) + float(k) * (float(upper) - float(pred))


def _power(device_id, flow, model_path, cache):
    if cache is not None:
        key = (device_id, round(float(flow), 1))
        if key in cache:
            return cache[key]
    r = PERF.estimate_power(device_id, float(flow), model_path=model_path)
    v = r["estimated_power_kw"] if r["status"] == "success" else None
    if cache is not None:
        cache[key] = v
    return v


def _bounds(cap, prev_flow, prev_running, cfg):
    lo, hi = cap.min_flow_m3min, cap.max_flow_m3min
    if cfg.ramp_enabled and prev_running and prev_flow is not None:
        rm = RAMP_STEP[cap.device_id]
        lo = max(lo, prev_flow - rm)
        hi = min(hi, prev_flow + rm)
    return lo, max(lo, hi)


def best_allocation(combo, required, caps, prev_flows, prev_running, model_path, cfg, cache):
    """最小功率分配 (满足 required, 受 min/max 与爬坡限制); 返回 flows/power/shortfall/low。"""
    combo = tuple(combo)
    if not combo:
        short = max(0.0, required)
        return {"flows": {}, "power_kw": 0.0, "shortfall": short, "low": False}
    bnd = {}
    for d in combo:
        bnd[d] = _bounds(caps[d], prev_flows.get(d), prev_running.get(d, False), cfg)
    # 均衡分配: 若需求足够 (>= n*lb_frac*RATED), 强制每台 >= lb_frac*RATED (无需过供即可消除低负荷)
    if cfg.balanced and required >= (cfg.lb_frac * RATED) * len(combo):
        for d in combo:
            bnd[d] = (max(bnd[d][0], cfg.lb_frac * RATED), bnd[d][1])
    # 机组间负荷均衡: 双机时把两台流量锁到 required/2 (最小化 |λ4-λ5|), 受边界约束
    if cfg.spread_balance and len(combo) == 2 and cfg.spread_tol <= 0.0:
        mid = required / 2.0
        for d in combo:
            lo = max(bnd[d][0], mid)
            hi = min(bnd[d][1], mid)
            if lo - 1e-6 <= hi + 1e-6:
                bnd[d] = (lo, max(lo, hi))
    best = None

    if len(combo) == 1:
        d = combo[0]
        f = min(max(required, bnd[d][0]), bnd[d][1])
        p = _power(d, f, model_path, cache)
        if p is None:
            return None
        best = {"flows": {d: f}, "power_kw": p, "device_power": {d: p},
                "shortfall": max(0.0, required - f), "low": (f / RATED) < LOW_LIMIT}
    else:
        d1, d2 = combo
        # 目标总量: required .. 允许过供(至上限和) 以脱离低负荷/满足下界
        lo_sum = bnd[d1][0] + bnd[d2][0]
        hi_sum = bnd[d1][1] + bnd[d2][1]
        total_lo = max(required, lo_sum)
        tol = cfg.spread_tol if (cfg.spread_balance and cfg.spread_tol > 0.0) else None
        for use_tol in ((tol, None) if tol is not None else (None,)):
            for tot in _lin(total_lo, hi_sum, cfg.grid, 8):
                extra = tot - lo_sum
                for r in _lin(0.0, 1.0, cfg.grid, None):
                    e1 = min(extra * r, bnd[d1][1] - bnd[d1][0])
                    f1 = bnd[d1][0] + e1
                    f2 = tot - f1
                    if f2 < bnd[d2][0] - 1e-6 or f2 > bnd[d2][1] + 1e-6:
                        continue
                    if use_tol is not None and abs(f1 - f2) > use_tol * RATED + 1e-6:
                        continue
                    p1 = _power(d1, f1, model_path, cache); p2 = _power(d2, f2, model_path, cache)
                    if p1 is None or p2 is None:
                        continue
                    cand = {"flows": {d1: f1, d2: f2}, "power_kw": p1 + p2,
                            "device_power": {d1: p1, d2: p2},
                            "shortfall": max(0.0, required - tot),
                            "low": (f1 / RATED < LOW_LIMIT) or (f2 / RATED < LOW_LIMIT)}
                    if best is None or cand["power_kw"] < best["power_kw"] - 1e-9:
                        best = cand
            if best is not None:
                break
    if best is None:
        return None
    # 过供保护: 总分不足时短供 (记录)
    return best


def _lin(a, b, step, n):
    import numpy as np
    if n is not None:
        return np.linspace(a, b, n)
    if b <= a:
        return np.array([a])
    count = min(2001, max(2, int(round((b - a) / step)) + 1))
    return np.linspace(a, b, count)


def _switch_events(prev, combo):
    return [d for d in DEVICE_IDS if (d in combo) != (d in prev)]


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


def plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
         model_path="artifacts/models/power_model.json", power_cap=0.0, cfg: UCConfig = None, cache=None) -> dict | None:
    cfg = cfg or UCConfig()
    H = len(required_seq)
    import itertools
    step_opts = []
    for k in range(H):
        d = required_seq[k]
        need = d + con.reserve.extra_m3_min(d)
        opts = {}
        for combo in COMBOS:
            for x in combo:
                if not caps[x].available:
                    break
            else:
                cap = sum(caps[x].max_flow_m3min for x in combo)
                reserve_ok = (cap + 1e-6 >= need) and (not combo or (cap - required_seq[k]) >= -1e-6)
                if combo and not reserve_ok:
                    continue
                if not combo and d > 1e-6:
                    continue
                # 分配 (首步用实际当前流量作爬坡基准; 后续步用上一k最优? 用当前流量近似)
                pf = cur_flows if k == 0 else {x: caps[x].min_flow_m3min for x in DEVICE_IDS}
                pr = {x: (x in cur_on) if k == 0 else True for x in DEVICE_IDS}
                al = best_allocation(combo, d, caps, pf, pr, model_path, cfg, cache)
                if al is None:
                    continue
                if power_cap > 0 and any((al["flows"].get(x, 0.0) > 0 and
                                          _power(x, al["flows"][x], model_path, cache) > power_cap + 1e-6) for x in combo):
                    continue
                opts[combo] = al
        step_opts.append(opts)
    if any(len(o) == 0 for o in step_opts):
        return None
    best = None
    dt_h = 15 / 60.0
    for seq in itertools.product(*[list(o.keys()) for o in step_opts]):
        if not _can_switch(cur_on, seq[0], as_of, last_switch, con):
            continue
        ok, _, _ = CONS.transition_feasibility(cur_on, seq[0], caps, required_seq[0], con.reserve)
        if not ok:
            continue
        prev, sw, low_steps, short, energy = set(cur_on), 0, 0, 0.0, 0.0
        for k in range(H):
            sw += len(_switch_events(prev, seq[k])); prev = set(seq[k])
            al = step_opts[k][seq[k]]
            energy += al["power_kw"] * dt_h
            short += al["shortfall"]
            low_steps += 1 if al["low"] else 0
        if sw > con.max_switches_per_hour:
            continue
        cost = cfg.w_short * short + energy + cfg.w_low * low_steps + cfg.w_switch * sw
        key = (round(cost, 6), sw, tuple(seq))
        if best is None or key < best[0]:
            best = (key, {"seq": [tuple(c) for c in seq], "switches": sw, "low_steps": low_steps,
                          "energy_kwh": round(energy, 4), "shortfall_m3min": round(short, 4),
                          "cost": round(cost, 4), "alloc0": step_opts[0][seq[0]]})
    return best[1] if best else None


def demand_for(pred, upper, cfg: UCConfig) -> float:
    if cfg.demand_basis == "upper":
        return float(upper)
    if cfg.demand_basis == "pred":
        return float(pred)
    if cfg.demand_basis == "mid":
        return 0.5 * (float(pred) + float(upper))
    if cfg.demand_basis == "pred_plus":
        return float(pred) + cfg.margin_m3min
    raise ValueError(cfg.demand_basis)
