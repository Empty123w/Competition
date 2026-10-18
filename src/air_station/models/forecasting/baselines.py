"""预测基线方法。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def persistence(y: pd.Series, horizon: int = 1) -> pd.Series:
    """持续性预测: 用当前值预测未来。"""
    return pd.Series(np.repeat(y.iloc[0], horizon)) if len(y) else pd.Series(dtype=float)


def seasonal_naive(y: pd.Series, season: int = 1440, horizon: int = 1) -> np.ndarray:
    """季节朴素: 用同一周期前的值(默认 1440 分钟=1天)。"""
    if len(y) <= season:
        return np.repeat(y.iloc[-1], horizon)
    vals = y.values
    out = []
    for h in range(1, horizon + 1):
        idx = len(vals) - season + (h - 1)
        out.append(vals[idx % len(vals)])
    return np.array(out, dtype=float)


def moving_average(y: pd.Series, window: int = 60, horizon: int = 1) -> np.ndarray:
    v = float(y.tail(window).mean()) if len(y) else np.nan
    return np.repeat(v, horizon)


def evaluate_baselines(y_true: np.ndarray, preds: dict[str, np.ndarray]) -> dict:
    """对多个基线计算 MAE/RMSE/MAPE。"""
    out = {}
    y_true = np.asarray(y_true, dtype=float)
    for name, p in preds.items():
        p = np.asarray(p, dtype=float)
        err = y_true - p
        mae = float(np.mean(np.abs(err)))
        rmse = float(np.sqrt(np.mean(err ** 2)))
        denom = np.maximum(np.abs(y_true), 1.0)
        mape = float(np.mean(np.abs(err) / denom) * 100)
        out[name] = {"mae": mae, "rmse": rmse, "mape": mape}
    return out
