"""基于阈值规则的健康扣分。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _penalty(value: float, warn: float, alarm: float) -> float:
    """返回 0~1 的扣分: warn 以下 0, alarm 以上 1, 之间线性。"""
    if value is None or np.isnan(value):
        return 0.0
    if value <= warn:
        return 0.0
    if value >= alarm:
        return 1.0
    return float((value - warn) / (alarm - warn))


def evaluate_rules(frame: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """对每个指标每条时间点计算扣分, 返回扣分宽表。"""
    out = pd.DataFrame(index=frame.index)
    for ind, th in rules.items():
        if ind not in frame.columns:
            continue
        out[ind] = frame[ind].apply(lambda v: _penalty(v, th["warn"], th["alarm"]))
    return out


def rule_summary(penalties: pd.DataFrame) -> tuple[float, dict]:
    """聚合规则扣分: 取各指标扣分的均值作为综合扣分, 并返回各指标贡献。"""
    if penalties.empty:
        return 0.0, {}
    per_indicator = penalties.mean(axis=0).to_dict()
    overall = float(np.mean(list(per_indicator.values()))) if per_indicator else 0.0
    return overall, per_indicator
