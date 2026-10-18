"""健康指数合成。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _level(health_index: float, levels: dict) -> str:
    if health_index >= levels.get("healthy", 80):
        return "healthy"
    if health_index >= levels.get("watch", 60):
        return "watch"
    if health_index >= levels.get("warning", 40):
        return "warning"
    return "critical"


def combine(rule_penalty: pd.Series, anomaly_score: pd.Series,
            config: dict) -> pd.DataFrame:
    """合成健康指数时间序列。"""
    weights = config.get("scoring", {}).get("weights", {"rule": 0.5, "anomaly": 0.5})
    levels = config.get("scoring", {}).get("levels", {})
    rp = rule_penalty.reindex(anomaly_score.index).fillna(0.0)
    an = anomaly_score.fillna(0.0)
    total = weights.get("rule", 0.5) * rp + weights.get("anomaly", 0.5) * an
    hi = (100.0 * (1.0 - total.clip(0, 1))).clip(0, 100)
    out = pd.DataFrame({"rule_penalty": rp, "anomaly_score": an, "health_index": hi})
    out["level"] = out["health_index"].apply(lambda v: _level(v, levels))
    return out


def top_indicators(row_penalties: pd.Series, k: int = 5) -> list[dict]:
    """返回贡献最大的若干指标。"""
    if row_penalties is None or len(row_penalties) == 0:
        return []
    s = row_penalties.sort_values(ascending=False).head(k)
    return [{"indicator": str(i), "contribution": float(v)} for i, v in s.items() if v > 0]
