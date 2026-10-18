"""无监督异常检测。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_zscore(frame: pd.DataFrame, window: int = 60,
                   threshold: float = 3.0) -> pd.Series:
    """滚动 z-score 异常评分(0~1)。"""
    scores = []
    for c in frame.columns:
        s = pd.to_numeric(frame[c], errors="coerce")
        mu = s.rolling(window, min_periods=max(5, window // 6)).mean()
        sd = s.rolling(window, min_periods=max(5, window // 6)).std()
        z = (s - mu) / sd.replace(0, np.nan)
        scores.append((z.abs() / threshold).clip(upper=1.0))
    if not scores:
        return pd.Series(0.0, index=frame.index)
    return pd.concat(scores, axis=1).max(axis=1).fillna(0.0)


def isolation_forest(frame: pd.DataFrame, baseline_end: str,
                     contamination: float = 0.02, random_state: int = 42) -> pd.Series:
    """IsolationForest 异常评分(0~1, 越大越异常)。用 baseline 期拟合。"""
    from sklearn.ensemble import IsolationForest

    X = frame.select_dtypes(include=[np.number]).fillna(frame.median(numeric_only=True))
    if X.empty:
        return pd.Series(0.0, index=frame.index)
    mask = pd.to_datetime(frame.index) <= pd.Timestamp(baseline_end)
    X_base = X[mask] if mask.any() else X
    if len(X_base) < 50:
        X_base = X
    model = IsolationForest(contamination=contamination, random_state=random_state)
    model.fit(X_base.values)
    # score_samples: 越低越异常; 转成 0~1, 越大越异常
    raw = -model.score_samples(X.values)
    lo, hi = np.nanmin(raw), np.nanmax(raw)
    norm = (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)
    return pd.Series(norm, index=frame.index)


def detect(frame: pd.DataFrame, config: dict) -> pd.Series:
    acfg = config.get("anomaly", {})
    method = acfg.get("method", "rolling_zscore")
    if method == "isolation_forest":
        return isolation_forest(frame, acfg.get("baseline_end", "2100-01-01"),
                                acfg.get("contamination", 0.02),
                                acfg.get("random_state", 42))
    return rolling_zscore(frame, acfg.get("rolling_window", 60),
                          acfg.get("zscore_threshold", 3.0))
