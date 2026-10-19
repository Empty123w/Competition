"""Selective Balanced Dispatch (SBD) — 在 B3(省电) 与 bal(均衡/降低负荷) 之间按步择优。

独立新模块 (不覆盖 B3 / UC / bal / cand / MPC)。
每步同时求解:
  - B3 计划: 最小功耗分配 (balanced=False);
  - bal 计划: 均衡分配 (balanced=True, 需求允许时每台 >= 134.45);
选择准则 (硬约束一致: 原供气目标/extra5%/容量/最低流量/爬坡/启停/B4):
  - 若 bal 未减少低负荷步 -> 选 B3;
  - 否则计算 额外电耗 extra=bal.energy-b3.energy 与 减少的低负荷步 avoided;
    仅当 extra <= max_extra_kwh_per_low_step * avoided 时选 bal, 否则 B3。
这样避免“固定采用 B3 或 bal”, 在能效—低负荷之间按阈值权衡。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from air_station.optimization import unit_commitment as UC
from air_station.optimization.unit_commitment import UCConfig


@dataclass
class SBDConfig:
    max_extra_kwh_per_low_step: float = 3.0   # 每减少一个低负荷步允许的额外 kWh
    w_switch: float = 2.0
    require_efficient: bool = True            # bal 运行机需 >= 134.45 (隐含由 balanced 保证)


def plan_sbd(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
             cfg: Optional[SBDConfig] = None,
             model_path="artifacts/models/power_model.json", power_cap=0.0, cache=None) -> Optional[dict]:
    cfg = cfg or SBDConfig()
    b3 = UC.plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
                 model_path=model_path, power_cap=power_cap,
                 cfg=UCConfig(demand_basis="upper", balanced=False), cache=cache)
    bal = UC.plan(required_seq, caps, con, cur_on, cur_flows, last_switch, as_of,
                  model_path=model_path, power_cap=power_cap,
                  cfg=UCConfig(demand_basis="upper", balanced=True), cache=cache)
    if b3 is None and bal is None:
        return None
    if b3 is None:
        bal = dict(bal); bal["chosen"] = "bal_fallback"; return bal
    if bal is None:
        b3 = dict(b3); b3["chosen"] = "b3_fallback"; return b3

    avoided = b3["low_steps"] - bal["low_steps"]
    extra = bal["energy_kwh"] - b3["energy_kwh"]
    choose_bal = (avoided > 0) and (extra <= cfg.max_extra_kwh_per_low_step * avoided + 1e-9)
    out = dict(bal if choose_bal else b3)
    out["chosen"] = "bal" if choose_bal else "b3"
    out["low_steps_b3"] = b3["low_steps"]
    out["low_steps_bal"] = bal["low_steps"]
    out["extra_kwh"] = round(extra, 4)
    out["avoided_low_steps"] = avoided
    return out
