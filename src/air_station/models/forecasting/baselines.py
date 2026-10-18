"""负荷预测基线方法 (阶段 A4)。

所有基线仅使用目标时刻之前的数据:
  - lag_1: 上一时刻值(值在 t, 预测 t+h);
  - ma_15: 最近 15 分钟平均值(截至 t);
  - same_time_yesterday: 前一天同一时刻(target 序列 shift(1440-h));
  - same_time_last_week: 前一周同一时刻(shift(10080-h), 数据范围允许时)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def baseline_series(target: pd.Series, name: str, horizon: int, cfg: dict) -> pd.Series:
    e = cfg.get("evaluate", {})
    day = int(e.get("seasonal_period_minutes", 1440))
    week = int(e.get("weekly_period_minutes", 10080))
    ma_w = int(e.get("moving_average_window_minutes", 15))
    if name == "lag_1":
        return target
    if name == "ma_15":
        return target.rolling(ma_w, min_periods=1).mean()
    if name == "same_time_yesterday":
        return target.shift(day - horizon)
    if name == "same_time_last_week":
        return target.shift(week - horizon)
    raise ValueError(f"未知基线: {name}")


def all_baselines(target: pd.Series, horizon: int, cfg: dict) -> dict[str, pd.Series]:
    return {name: baseline_series(target, name, horizon, cfg)
            for name in cfg.get("evaluate", {}).get(
                "baselines", ["lag_1", "ma_15", "same_time_yesterday", "same_time_last_week"])}


def signed_residual_quantiles(y_true: np.ndarray, y_pred: np.ndarray,
                              q_lo: float, q_hi: float) -> tuple[float, float]:
    r = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return 0.0, 0.0
    return float(np.quantile(r, q_lo)), float(np.quantile(r, q_hi))
