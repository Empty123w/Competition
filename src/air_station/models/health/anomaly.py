"""无监督异常评分 (阶段 A5)。

直接使用 A3 的 health_features.parquet 中 `dev_*`(相对同负荷区间正常基准的偏差),
按阈值配置的 dev_scale 归一化, 不重新计算特征。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def anomaly_score_series(frame: pd.DataFrame, dev_features: list[str],
                         dev_scale: float = 3.0) -> pd.Series:
    cols = [c for c in dev_features if c in frame.columns]
    if not cols:
        return pd.Series(0.0, index=frame.index)
    vals = frame[cols].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        score = np.nanmax(np.abs(vals) / dev_scale, axis=1)
    score = np.clip(score, 0.0, 1.0)
    return pd.Series(np.nan_to_num(score, nan=0.0), index=frame.index)


def top_deviation(row: pd.Series, dev_features: list[str]) -> tuple[str | None, float]:
    """返回偏离最大的 dev 特征与其值。"""
    best, best_v = None, -1.0
    for c in dev_features:
        if c in row.index and not pd.isna(row[c]):
            v = abs(float(row[c]))
            if v > best_v:
                best, best_v = c, v
    return best, (best_v if best else 0.0)
