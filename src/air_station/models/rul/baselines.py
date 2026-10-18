"""RUL 趋势外推基线。

思路: 以振动滚动均值(vib_roll)为退化指标, 估计其对时间的斜率(每小时),
外推到阈值 threshold 得到剩余时长, 作为 RUL 估计。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trend_extrapolation(df: pd.DataFrame, cfg: dict, group_col: str = "episode_id") -> pd.Series:
    b = cfg.get("baseline", {})
    feature = b.get("indicator", "vib_roll")
    threshold = float(b.get("threshold", 18.0))
    step_hours = float(cfg.get("step_hours", 6.0))
    init_rul = float(cfg.get("initial_rul_hours", 2000.0))
    eps = 1e-6

    g = df.groupby(group_col, observed=True)[feature]
    slope_step = g.diff().fillna(0.0)
    rate = (slope_step / step_hours).clip(lower=eps)              # 每小时增量
    rul_est = (threshold - df[feature]) / rate
    rul_est = rul_est.clip(lower=0.0, upper=init_rul)
    return pd.Series(rul_est.to_numpy(), index=df.index, name="rul_baseline")
