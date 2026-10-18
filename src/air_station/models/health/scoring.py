"""健康评分合成与风险分级 (阶段 A5)。阈值来自 health_thresholds.yaml。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def combine(rule_penalty, anomaly_score, thresholds: dict):
    w = thresholds.get("scoring", {})
    wr = float(w.get("weight_rule", 0.5))
    wa = float(w.get("weight_anomaly", 0.5))
    total = wr * np.asarray(rule_penalty, dtype=float) + wa * np.asarray(anomaly_score, dtype=float)
    score = 100.0 * (1.0 - np.clip(total, 0.0, 1.0))
    return score


def risk_level(score: float, thresholds: dict) -> str:
    levels = thresholds.get("levels", {})
    labels = thresholds.get("labels", {})
    if score >= levels.get("normal", 85):
        return labels.get("normal", "正常")
    if score >= levels.get("attention", 70):
        return labels.get("attention", "关注")
    if score >= levels.get("warning", 50):
        return labels.get("warning", "预警")
    return labels.get("critical", "严重")


def level_series(scores, thresholds: dict) -> pd.Series:
    return pd.Series([risk_level(float(s), thresholds) for s in scores])
