"""第二阶段: 压力感知闭环仿真 (MPC / 反应式 PI 对照)。

目的: 在**相同物理仿真**(沿用 simulation/closed_loop 的质量守恒与工程假设)下, 比较
  - reactive_pi : 现有反应式控制 (瞬时预测 + PI 偏置) —— 基线
  - mpc         : 多时段滚动 MPC, 使用**未来需求前瞻**与解析压力模型
  - mpc_ml      : 同上, 压力模型改用 ML (LightGBM) 环境模型

关键: 只比较**控制策略**差异; 物理容量不足 (peak/fault) 场景如实报告, 不以虚增容量伪装达标。
不修改 simulation/closed_loop.py (冻结); 本模块独立实现控制器。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd

from air_station.optimization import performance as PERF
from air_station.simulation import closed_loop as CL
from air_station.stage2 import pressure_model as PM

RATED = 268.9
DEVICES = ("4#", "5#")


@dataclass
class CtrlParams:
    dt_min: float = 5.0
    exec_delay_steps: int = 1
    forecast_noise_frac: float = 0.03
    forecast_ub_margin_frac: float = 0.05
    forecast_ub_const: float = 8.0
    pi_kp: float = 1500.0
    pi_ki: float = 25.0
    pi_bias_max: float = 150.0
    horizon: int = 4                 # MPC 前瞻步数
    w_pressure: float = 5000.0       # 压力越界权重 (MPa 归一)
    w_center: float = 120000.0         # 压力带内偏离设定点权重
    w_energy: float = 1.0            # 能耗权重 (kWh)
    w_short: float = 0.0             # 缺供权重 (由压力下界违规隐式惩罚)
    w_smooth: float = 0.0            # 供气平滑权重 (抑制动作抖动)
    seed: int = 0


def _power(flows, model, cache):
    tot = 0.0
    for d, q in flows.items():
        if q and q > 0:
            key = (d, round(float(q), 1))
            if key not in cache:
                r = PERF.estimate_power(d, float(q), model_path=model)
                cache[key] = r["estimated_power_kw"] if r["status"] == "success" else None
            if cache[key] is None:
                return float("nan")
            tot += cache[key]
    return tot


def allocate(S: float, model: str, cache: dict, avail=DEVICES) -> dict:
    """把总供气 S 映射到可用机组流量 (min 120 / max 268.9, 最小功率)。"""
    if S <= 1e-6:
        return {"4#": 0.0, "5#": 0.0}
    best, best_p = None, None
    combos = [c for c in (("4#",), ("5#",), ("4#", "5#")) if all(d in avail for d in c)]
    if not combos:
        return {"4#": 0.0, "5#": 0.0}
    for combo in combos:
        if S > RATED * len(combo) + 1e-6:
            continue
        if S < 120.0 * len(combo) - 1e-6 and S > 0:
            # 低于最低稳定流量 -> 供气按最低稳定流量 (放空由压力环节显式记账)
            tot = 120.0 * len(combo)
        else:
            tot = S
        # 均分
        parts = [tot / len(combo)] * len(combo)
        parts = [min(max(x, 120.0), RATED) for x in parts]
        flows = {d: 0.0 for d in DEVICES}
        for d, x in zip(combo, parts):
            flows[d] = x
        p = _power(flows, model, cache)
        if p is None:
            continue
        if best_p is None or p < best_p:
            best, best_p = flows, p
    if best is None:
        best = {"4#": min(RATED, max(120.0, S / 1.0)), "5#": 0.0}
    return best


def _viol(p, lo, hi):
    return max(0.0, lo - p) + max(0.0, p - hi)


def mpc_decide(p, dem_future, ccfg: CtrlParams, sim: CL.SimParams, model, cache,
               pmodel: Optional[PM.PressureModel] = None, lookahead: int = 4, avail=DEVICES,
               prev_S: float = 0.0):
    """滚动 MPC: 在候选总供气上最小化 (压力越界 + 能耗 + 缺供)。返回 (S, flows)。"""
    grid = np.arange(0.0, 2 * RATED + 1.0, 5.0)
    best = None
    for S in grid:
        pp = float(p)
        cost = 0.0
        for k in range(lookahead):
            d = float(dem_future[k]) if k < len(dem_future) else float(dem_future[-1])
            q_blow = CL.blowoff_flow(pp, S, sim)
            if pmodel is not None:
                pp = float(pmodel.step(pp, S, d, q_blow)[0])
            else:
                pp = CL.pressure_step(pp, S, d, q_blow, sim)
            cost += ccfg.w_pressure * _viol(pp, sim.band_lo_mpa, sim.band_hi_mpa)
            cost += ccfg.w_center * (pp - sim.p_set_mpa) ** 2
            cost += ccfg.w_short * max(0.0, d - S)
        flows = allocate(S, model, cache, avail) if S > 0 else {"4#": 0.0, "5#": 0.0}
        cost += ccfg.w_energy * _power(flows, model, cache) * (ccfg.dt_min / 60.0)
        cost += ccfg.w_smooth * abs(S - prev_S)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, S, flows)
    return best[1], best[2]


def simulate(strategy: str, sc: CL.Scenario, ccfg: CtrlParams = None,
             sim: CL.SimParams = None, config_path: str = "configs/optimization.yaml",
             pmodel: Optional[PM.PressureModel] = None) -> dict:
    ccfg = ccfg or CtrlParams()
    sim = sim or CL.SimParams()
    ccfg.dt_min = sim.dt_min
    from air_station.optimization.service import load_optimization_config
    model = load_optimization_config(config_path)["paths"]["power_model"]
    rng = np.random.default_rng(ccfg.seed)
    dem = CL.demand_series(sc)
    n = sc.n_steps

    p = sim.p_init_mpa
    flows = {d: sc.base_m3min / 2.0 for d in DEVICES}
    bias_i = 0.0
    pending = []
    cache = {}
    recs = []
    prev_S = float(sc.base_m3min)
    for k in range(n):
        t = pd.Timestamp("2026-01-01 00:00:00") + pd.Timedelta(minutes=sim.dt_min * k)
        avail = set(DEVICES)
        if sc.fault and k >= sc.fault[0]:
            avail.discard(sc.fault[1])
        if strategy == "reactive_pi":
            pred = float(dem[k]) + rng.normal(0, ccfg.forecast_noise_frac * max(50.0, dem[k]))
            e = sim.p_set_mpa - p
            bias_i = float(np.clip(bias_i + ccfg.pi_ki * e, -ccfg.pi_bias_max, ccfg.pi_bias_max))
            bias = float(np.clip(ccfg.pi_kp * e + bias_i, -ccfg.pi_bias_max, ccfg.pi_bias_max))
            S = max(0.0, pred + bias)
            S = S * (1 + ccfg.forecast_ub_margin_frac) + ccfg.forecast_ub_const
            acts = allocate(S, model, cache, avail)
        else:  # mpc / mpc_ml
            fut = []
            for j in range(ccfg.horizon):
                kk = min(n - 1, k + j)
                fut.append(float(dem[kk]) + rng.normal(0, ccfg.forecast_noise_frac * max(50.0, dem[kk])))
            if strategy == "mpc":
                _, acts = mpc_decide(p, fut, ccfg, sim, model, cache, None, ccfg.horizon, avail, prev_S)
            else:
                _, acts = mpc_decide(p, fut, ccfg, sim, model, cache, pmodel, ccfg.horizon, avail, prev_S)

        # 执行延迟 (统一)
        if ccfg.exec_delay_steps > 0:
            pending.append([ccfg.exec_delay_steps, dict(acts)])
            ready = None
            for it in pending:
                it[0] -= 1
                if it[0] <= 0 and ready is None:
                    ready = it[1]
            pending = [it for it in pending if it[0] > 0]
            applied = ready if ready is not None else acts
        else:
            applied = acts
        flows = applied
        prev_S = sum(flows[d] for d in DEVICES)

        q_sup = sum(flows[d] for d in DEVICES)
        q_dem = float(dem[k])
        q_blow = CL.blowoff_flow(p, q_sup, sim)
        p_before = p
        p, vent, unmet, flag = CL.pressure_step_accounted(p, q_sup, q_dem, q_blow, sim)
        power = _power({d: flows[d] for d in DEVICES}, model, cache)
        recs.append({"k": k, "demand": q_dem, "supply": q_sup, "blow": q_blow, "pressure": p,
                     "power_kw": power, "f4": flows.get("4#", 0.0), "f5": flows.get("5#", 0.0),
                     "vent_m3": vent, "unmet_m3": unmet, "clamp": flag})
    return {"strategy": strategy, "scenario": sc.name, "records": recs,
            "metrics": _metrics(recs, sim)}


def _metrics(recs, sim: CL.SimParams) -> dict:
    df = pd.DataFrame(recs)
    dt_h = sim.dt_min / 60.0
    E = float(df.power_kw.fillna(0).sum()) * dt_h
    V = float(df.supply.sum()) * sim.dt_min
    D = float(df.demand.sum()) * sim.dt_min
    comp = float(((df.pressure >= sim.band_lo_mpa) & (df.pressure <= sim.band_hi_mpa)).mean())
    unmet = float(df.get("unmet_m3", pd.Series(dtype=float)).sum())
    return {"steps": int(len(df)),
            "pressure_compliance": round(comp, 4),
            "pressure_std": round(float(df.pressure.std()), 5),
            "pressure_min": round(float(df.pressure.min()), 4),
            "pressure_max": round(float(df.pressure.max()), 4),
            "energy_kwh": round(E, 1), "supply_m3": round(V, 1), "demand_m3": round(D, 1),
            "sec_kwh_per_m3": round(E / V, 6) if V else None,
            "supply_gap_m3": round(float((df.demand - df.supply).clip(lower=0).sum()) * sim.dt_min, 1),
            "demand_satisfaction": round(1 - float((df.demand - df.supply).clip(lower=0).sum()) / float(df.demand.sum()), 4),
            "unserved_deficit_m3": round(unmet, 1),
            "blowoff_m3": round(float(df.blow.sum()) * sim.dt_min, 1)}
