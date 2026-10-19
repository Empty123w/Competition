"""Adaptive Robust Dispatch (ARD) — 独立新优化器 (不覆盖 B3 / UC / DP / MPC)。

组成:
  1) 因果预测误差修正: 仅用 **目标时刻 <= as_of 已实现** 的 (actual-pred) 残差,
     EWMA 或滚动分位数估计需求偏差 (无未来泄漏)。
  2) 动态需求安全裕量: 每步 demand_h = predicted_h + margin_h,
     margin_h = clamp(偏差估计, 0, ub_h - pred_h) —— 在 pred 与 upper 之间自适应。
  3) 峰值提前响应 (可选): 若时域内预测上升超过阈值, 提升首步需求以促使提前开机 (仍受约束)。
  4) 联合调度: 复用 UC 的组合枚举 + 连续分配 + 爬坡/最短开停/B4。
  5) 安全回退: 异常/不可行/超时 -> 返回 None, 由调用方保守保持。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from air_station.optimization import unit_commitment as UC


@dataclass
class ARDConfig:
    method: str = "ewma"          # ewma | quantile | none
    alpha: float = 0.15           # EWMA 系数
    q: float = 0.9                # 分位数 (quantile 方法)
    window: int = 720             # 滚动窗口步数 (0=全历史)
    cap_to_upper: bool = True     # margin 不超过 (upper-pred)
    peak_lead: bool = False       # 峰值提前响应
    peak_threshold: float = 25.0  # 时域内 pred 上升阈值 (m3/min)
    w_low: float = 0.0
    w_switch: float = 2.0
    min_history: int = 20         # 残差估计所需最少样本


class ResidualEstimator:
    """因果残差估计 (仅使用 target_time <= as_of 的已实现样本)。"""

    def __init__(self, cfg: ARDConfig):
        self.cfg = cfg
        self._res: list[tuple[pd.Timestamp, float]] = []   # (target_time, resid)

    def update(self, target_time, actual, pred) -> None:
        if actual is None or pred is None or not np.isfinite(actual) or not np.isfinite(pred):
            return
        self._res.append((pd.Timestamp(target_time), float(actual) - float(pred)))

    def _realized(self, as_of):
        t = pd.Timestamp(as_of)
        r = [v for (tt, v) in self._res if tt <= t]
        if self.cfg.window and len(r) > self.cfg.window:
            r = r[-self.cfg.window:]
        return np.asarray(r, float)

    def margin(self, as_of, pred, ub, cfg: ARDConfig) -> float:
        if cfg.method == "none":
            return 0.0
        r = self._realized(as_of)
        if len(r) < cfg.min_history:
            # 历史不足 -> 保守: 用 upper 的下半段 (不做激进下调)
            return min(max(0.0, float(ub) - float(pred)), 0.5 * (float(ub) - float(pred)))
        if cfg.method == "ewma":
            a = cfg.alpha
            m = r[0]
            for x in r[1:]:
                m = a * x + (1 - a) * m
            base = m
        else:  # quantile
            base = float(np.quantile(r, cfg.q))
        base = max(0.0, base)
        if cfg.cap_to_upper:
            base = min(base, max(0.0, float(ub) - float(pred)))
        return base

    def margin_by_horizon(self, as_of, preds, ubs, cfg: ARDConfig):
        return [self.margin(as_of, p, u, cfg) for p, u in zip(preds, ubs)]


def demand_sequence(preds, ubs, est: ResidualEstimator, as_of, cfg: ARDConfig):
    """返回 per-horizon 需求序列 (pred + margin, 可选峰值提前)。"""
    margins = est.margin_by_horizon(as_of, preds, ubs, cfg)
    demand = [float(p) + m for p, m in zip(preds, margins)]
    if cfg.peak_lead and preds:
        rise = max(preds) - preds[0]
        if rise > cfg.peak_threshold:
            demand[0] = max(demand[0], float(max(preds)))   # 首步按峰值需求, 促提前开机
    # 不超 upper (避免超过模型上界的过度保守)
    demand = [min(d, float(u)) for d, u in zip(demand, ubs)]
    return demand, margins


def plan_ard(preds, ubs, caps, con, cur_on, cur_flows, last_switch, as_of,
             est: ResidualEstimator, cfg: ARDConfig,
             model_path="artifacts/models/power_model.json", power_cap=0.0, cache=None) -> Optional[dict]:
    demand, margins = demand_sequence(preds, ubs, est, as_of, cfg)
    b = UC.plan(demand, caps, con, cur_on, cur_flows, last_switch, as_of,
                model_path=model_path, power_cap=power_cap,
                cfg=UC.UCConfig(w_low=cfg.w_low, w_switch=cfg.w_switch,
                                demand_basis="upper"), cache=cache)
    if b is not None:
        b["demand_seq"] = [round(d, 2) for d in demand]
        b["margins"] = [round(m, 2) for m in margins]
    return b
