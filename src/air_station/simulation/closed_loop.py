"""工业空压站闭环仿真 (B 侧; 全部无法由真实数据确定的参数标为工程假设)。

目的: 在**明确模拟工况**下, 闭环比较 B1/B3/bal/LRB 的 压力合格率/波动/供气/能耗/安全,
      为"压力合格率 ≥99.5%、波动下降 ≥30%"提供可复现的工程场景证据 (非现场达标)。

组成:
  - 独立模拟工厂用气需求 Q_dem(t) (与空压机供气无关, 严格区别于历史供气代理);
  - 储气/管网等效容积 K_VOL (m3/MPa) 与泄漏;
  - 质量平衡压力动态 p_{k+1}=p_k+dt*((Q_sup-Q_dem-Q_blow)/K_VOL - LEAK*(p-p_env));
  - 放空 BOV 比例泄放; 压力反馈 PI 偏置需求目标;
  - 调度由 dispatch_api (b3/balanced/load_balanced) 或 B1 规则给出; 含执行延迟/预测误差/设备异常;
  - 设备容量/最低稳定流量/爬坡由 DispatchConstraints + 功率模型约束。

严格区分: 真实 SCADA 测量 / 历史供气代理 / **模拟用气需求**。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization import dispatch_api as API
from air_station.optimization import performance as PERF

RATED = 268.9
DEVICES = ("4#", "5#")


@dataclass
class SimParams:
    # --- 储气/网络 (工程假设, 见 sensitivity) ---
    k_vol_m3_per_mpa: float = 23700.0     # 等效储气容积 (m3 自由空气 / MPa)
    leak_per_min: float = 0.001            # 泄漏系数 (1/min)
    p_env_mpa: float = 0.55
    p_init_mpa: float = 0.60
    p_hard_lo: float = 0.40                # 仅数值保护
    p_hard_hi: float = 0.75
    # --- 压力控制 ---
    p_set_mpa: float = 0.60
    band_lo_mpa: float = 0.55              # 合格带 (工程示例, 非官方)
    band_hi_mpa: float = 0.66
    pi_kp: float = 1500.0                  # PI: m3/min per MPa
    pi_ki: float = 25.0                    # 积分增益 (每步)
    pi_bias_max: float = 150.0             # 偏置上限 (m3/min)
    blow_k: float = 1500.0                 # 放空增益 (m3/min per MPa 超带)
    blow_deadband: float = 0.003
    # --- 执行/预测 ---
    dt_min: float = 5.0
    exec_delay_steps: int = 1
    forecast_noise_frac: float = 0.03      # 相对噪声 (std)
    forecast_bias_frac: float = 0.0
    forecast_ub_margin_frac: float = 0.05  # 上界=预测*(1+margin)+const
    forecast_ub_const: float = 8.0
    seed: int = 0


@dataclass
class Scenario:
    name: str
    n_steps: int = 288                     # 24h @5min
    base_m3min: float = 430.0
    amp_m3min: float = 40.0
    period_steps: float = 288.0
    step_change: Optional[tuple[int, float]] = None   # (step, new_base)
    fault: Optional[tuple[int, str]] = None           # (step, device_id) -> 该机不可用
    note: str = ""


SCENARIOS = {
    "normal": Scenario("normal", base_m3min=430, amp_m3min=40, note="正常负荷"),
    "low": Scenario("low", base_m3min=250, amp_m3min=25, note="低负荷/大马拉小车"),
    "peak": Scenario("peak", base_m3min=500, amp_m3min=20, note="高峰近容量"),
    "step": Scenario("step", base_m3min=300, amp_m3min=30, step_change=(144, 480), note="负荷突增"),
    "fault": Scenario("fault", base_m3min=430, amp_m3min=40, fault=(144, "5#"), note="设备异常(5#)"),
}


def demand_series(sc: Scenario) -> np.ndarray:
    k = np.arange(sc.n_steps)
    base = sc.base_m3min + sc.amp_m3min * np.sin(2 * np.pi * k / sc.period_steps - np.pi / 2)
    if sc.step_change:
        i0, nb = sc.step_change
        base = base.copy(); base[i0:] = nb + sc.amp_m3min * 0.5 * np.sin(2 * np.pi * (k[i0:] - i0) / sc.period_steps)
    return np.clip(base, 0.0, 2 * RATED)


def pressure_step(p: float, q_sup: float, q_dem: float, q_blow: float, params: SimParams) -> float:
    """质量平衡单步压力更新 (含泄漏与硬钳制)。"""
    dp = params.dt_min * ((q_sup - q_dem - q_blow) / params.k_vol_m3_per_mpa
                          - params.leak_per_min * (p - params.p_env_mpa))
    return float(np.clip(p + dp, params.p_hard_lo, params.p_hard_hi))


def blowoff_flow(p: float, q_sup: float, params: SimParams) -> float:
    """BOV 比例泄放 (仅当压力超带)。"""
    if p > params.band_hi_mpa + params.blow_deadband:
        return float(min(q_sup, params.blow_k * (p - (params.band_hi_mpa + params.blow_deadband))))
    return 0.0


def _power(flows, model, cache) -> float:
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


def _snapshot(t, p, running, flows):
    devs = [C.DeviceSnapshot(d, bool(running[d]), float(flows[d]) if running[d] else 0.0,
                             None, p, None, None, quality_valid=bool(running.get(f"q_{d}", True)))
            for d in DEVICES]
    return C.StationSnapshot(str(t), float(p), devs, "simulation")


def _forecast(t, pred, ub):
    return C.ForecastResult(str(t), 15, float(pred), float(max(0.0, pred - 15.0)), float(ub), "sim")


def _b1_supply(snap, fc, con, base_cfg, state):
    r = B1.decide(snap, fc, [], con, base_cfg, state)
    if r.status != C.STATUS_SUCCESS:
        return None
    return {a.device_id: (a.target_flow_m3_min or 0.0) for a in r.device_actions if a.running_after}, r


def _apply(flows, snap):
    running = {d: (flows.get(d, 0.0) > 0) for d in DEVICES}
    return running, {d: float(flows.get(d, 0.0)) for d in DEVICES}


def simulate(strategy: str, sc: Scenario, params: SimParams = None,
             config_path: str = "configs/optimization.yaml", base_config: str = "configs/baseline.yaml") -> dict:
    """闭环仿真。strategy ∈ {b1, b3, balanced, load_balanced}。"""
    p = params or SimParams()
    con = C.DispatchConstraints.from_config(B1.load_config(base_config))
    base_cfg = B1.load_config(base_config)
    from air_station.optimization.service import load_optimization_config
    model = load_optimization_config(config_path)["paths"]["power_model"]
    rng = np.random.default_rng(p.seed)
    dem = demand_series(sc)

    state = C.PolicyState()
    running = {d: True for d in DEVICES}
    flows = {d: sc.base_m3min / 2.0 for d in DEVICES}
    pressure = p.p_init_mpa
    bias_i = 0.0
    pending = []  # (delay_remaining, flows)
    cache = {}
    con_f = con
    recs = []
    for k in range(sc.n_steps):
        t = pd.Timestamp("2026-01-01 00:00:00") + pd.Timedelta(minutes=p.dt_min * k)
        snap = _snapshot(t, pressure, running, flows)
        # 预测 (含噪声/偏差; 上界宽) + PI 压力偏置
        pred = float(dem[k]) * (1 + p.forecast_bias_frac) + rng.normal(0, p.forecast_noise_frac * max(50.0, dem[k]))
        e = p.p_set_mpa - pressure
        bias_i = float(np.clip(bias_i + p.pi_ki * e, -p.pi_bias_max, p.pi_bias_max))
        bias = float(np.clip(p.pi_kp * e + bias_i, -p.pi_bias_max, p.pi_bias_max))
        ub = max(0.0, pred + bias) * (1 + p.forecast_ub_margin_frac) + p.forecast_ub_const
        fc = _forecast(t, max(0.0, pred + bias), ub)

        # 设备异常: 设为不可用 (quality_valid=False -> capabilities available=False)
        if sc.fault and k >= sc.fault[0]:
            snap = C.StationSnapshot(str(t), float(pressure),
                                     [C.DeviceSnapshot(d.device_id, d.running, d.flow_m3_min, None, pressure,
                                                       None, None, quality_valid=(d.device_id != sc.fault[1]))
                                      for d in snap.devices], "simulation")

        # 决策
        fb = False
        if strategy == "b1":
            res = _b1_supply(snap, fc, con_f, base_cfg, state)
            if res is None:
                acts = None; fb = True
            else:
                acts = res[0]
        else:
            out = API.plan_dispatch(strategy, snap, fc, [], con_f, state=state)
            if out.get("status") == C.STATUS_SUCCESS and not out.get("is_fallback"):
                acts = {a["device_id"]: (a["target_flow_m3_min"] or 0.0) for a in out["device_actions"] if a["running_after"]}
            else:
                acts = None; fb = True
        if acts is None:  # 安全回退: 维持当前
            acts = {d: (flows[d] if running[d] else 0.0) for d in DEVICES}

        # 执行延迟
        if p.exec_delay_steps > 0:
            pending.append([p.exec_delay_steps, dict(acts)])
            ready = None
            for item in pending:
                item[0] -= 1
                if item[0] <= 0 and ready is None:
                    ready = item[1]
            pending = [it for it in pending if it[0] > 0]
            applied = ready if ready is not None else acts
        else:
            applied = acts
        running, flows = _apply(applied, snap)

        # 供气/需求/放空/压力
        q_sup = sum(flows[d] for d in DEVICES if running[d])
        q_dem = float(dem[k])
        q_blow = blowoff_flow(pressure, q_sup, p)
        pressure = pressure_step(pressure, q_sup, q_dem, q_blow, p)

        power = _power({d: flows[d] for d in DEVICES if running[d]}, model, cache)
        recs.append({"k": k, "t": str(t), "demand": q_dem, "supply": q_sup, "blow": q_blow,
                     "pressure": pressure, "power_kw": power,
                     "f4": flows.get("4#", 0.0), "f5": flows.get("5#", 0.0),
                     "fallback": fb})
    return {"strategy": strategy, "scenario": sc.name, "params": asdict(p), "records": recs,
            "metrics": _metrics(recs, p)}


def _metrics(recs, p: SimParams) -> dict:
    df = pd.DataFrame(recs)
    dt_h = p.dt_min / 60.0
    E = float(df["power_kw"].fillna(0).sum()) * dt_h
    V = float(df["supply"].sum()) * p.dt_min
    D = float(df["demand"].sum()) * p.dt_min
    comp = float(((df["pressure"] >= p.band_lo_mpa) & (df["pressure"] <= p.band_hi_mpa)).mean())
    low = float((((df.f4 > 0) & (df.f4 / RATED < 0.5)).sum() + ((df.f5 > 0) & (df.f5 / RATED < 0.5)).sum()) * p.dt_min)
    # 质量守恒: 净入流(m3) - 储气变化(m3) - 泄漏项(m3) ~ 0
    inflow = (float(df.supply.sum()) - float(df.demand.sum()) - float(df.blow.sum())) * p.dt_min
    store = p.k_vol_m3_per_mpa * (float(df.pressure.iloc[-1]) - float(df.pressure.iloc[0]))
    leak = p.k_vol_m3_per_mpa * p.leak_per_min * float((df.pressure - p.p_env_mpa).sum()) * p.dt_min
    residual = inflow - store - leak
    return {
        "steps": int(len(df)),
        "pressure_compliance": round(comp, 4),
        "pressure_mean": round(float(df.pressure.mean()), 4),
        "pressure_std": round(float(df.pressure.std()), 5),
        "pressure_min": round(float(df.pressure.min()), 4),
        "pressure_max": round(float(df.pressure.max()), 4),
        "energy_kwh": round(E, 1), "supply_m3": round(V, 1), "demand_m3": round(D, 1),
        "sec_kwh_per_m3": round(E / V, 6) if V else None,
        "supply_gap_m3": round(float((df.demand - df.supply).clip(lower=0).sum()) * p.dt_min, 1),
        "over_supply_m3": round(float((df.supply - df.demand).clip(lower=0).sum()) * p.dt_min, 1),
        "blowoff_m3": round(float(df.blow.sum()) * p.dt_min, 1),
        "low_load_min": low,
        "fallback_steps": int(df.fallback.sum()),
        "mass_balance_residual_m3": round(residual, 1),
        "pressure_terminal": round(float(df.pressure.iloc[-1]), 4),
    }
