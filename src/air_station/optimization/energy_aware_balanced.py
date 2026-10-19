"""能量感知均衡调度 (Energy-Aware Balanced, EAB)。

理论依据: 低负荷运行的真实能效损失远小于“为脱离低负荷而均衡分配”的额外能耗
(见 reports/b/dev_valid_diag.json 与 oracle_split.json)。因此均衡只在**其额外能耗可接受**
时启用; 否则退回最小能耗分配。这样在保持低负荷改善的同时, 避免在高能耗工况额外耗电。

实现: 复用 UC.plan, 分别求:
  E0 = 最小能耗方案 (balanced=False)
  E1 = 均衡方案 (balanced=True, 下界 lb_frac*RATED)
若 E1 的时域总能耗超出 E0 不超过 cost_cap_kwh_per_step * H, 采用均衡方案; 否则采用最小能耗方案。
两种方案共用同一硬约束/备用/爬坡/安全口径, 仅分配不同。
"""
from __future__ import annotations

from dataclasses import dataclass

from air_station.optimization import unit_commitment as UC

DT_H = 15 / 60.0


@dataclass
class EABConfig:
    lb_frac: float = 0.5            # 均衡下界比例 (0.5 -> 134.45)
    cost_cap_kwh_per_step: float = float("inf")  # 每步允许的最大额外能耗
    demand_basis: str = "upper"
    ramp_enabled: bool = True


def plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
         model_path="artifacts/models/power_model.json", power_cap=0.0,
         cfg: EABConfig = None, cache=None) -> dict | None:
    cfg = cfg or EABConfig()
    base = dict(ramp_enabled=cfg.ramp_enabled, demand_basis=cfg.demand_basis)
    e0 = UC.plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
                 model_path=model_path, power_cap=power_cap,
                 cfg=UC.UCConfig(balanced=False, **base), cache=cache)
    if cfg.lb_frac <= 0.5 or cfg.cost_cap_kwh_per_step <= 0.0:
        return e0
    e1 = UC.plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
                 model_path=model_path, power_cap=power_cap,
                 cfg=UC.UCConfig(balanced=True, lb_frac=cfg.lb_frac, **base), cache=cache)
    if e1 is None:
        return e0
    if e0 is None:
        return e1
    H = len(required_seq)
    extra = e1["energy_kwh"] - e0["energy_kwh"]
    if extra <= cfg.cost_cap_kwh_per_step * H + 1e-9:
        return e1
    return e0
