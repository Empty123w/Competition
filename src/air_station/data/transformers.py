"""特征变换工具: 读取已处理数据、重采样、切分、日历与滞后/滚动特征。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TIME_COL = "time"


def load_processed(processed_dir: str | Path, name: str) -> pd.DataFrame:
    """读取 processed 目录下的表 (parquet 优先, 回退 csv)。"""
    d = Path(processed_dir)
    pq = d / f"{name}.parquet"
    csv = d / f"{name}.csv"
    if pq.exists():
        df = pd.read_parquet(pq)
    elif csv.exists():
        df = pd.read_csv(csv)
    else:
        raise FileNotFoundError(f"未找到 {pq} 或 {csv}")
    if TIME_COL in df.columns:
        df[TIME_COL] = pd.to_datetime(df[TIME_COL])
    return df


def resample(df: pd.DataFrame, rule: str, agg: str = "mean",
             time_col: str = TIME_COL) -> pd.DataFrame:
    """按时间规则重采样(默认均值)。"""
    out = df.set_index(time_col).resample(rule).agg(agg).reset_index()
    return out


def split_by_time(df: pd.DataFrame, train_end: str, valid_end: str,
                  time_col: str = TIME_COL) -> dict[str, pd.DataFrame]:
    """按时间切分为 train/valid/test。"""
    t = df[time_col]
    train = df[t <= pd.Timestamp(train_end)]
    valid = df[(t > pd.Timestamp(train_end)) & (t <= pd.Timestamp(valid_end))]
    test = df[t > pd.Timestamp(valid_end)]
    return {"train": train.reset_index(drop=True),
            "valid": valid.reset_index(drop=True),
            "test": test.reset_index(drop=True)}


def add_calendar(df: pd.DataFrame, time_col: str = TIME_COL) -> pd.DataFrame:
    df = df.copy()
    t = df[time_col]
    df["hour"] = t.dt.hour
    df["minute"] = t.dt.minute
    df["dow"] = t.dt.dayofweek
    df["is_weekend"] = (t.dt.dayofweek >= 5).astype(int)
    return df


def add_lag_rolling(df: pd.DataFrame, target: str, lags: list[int],
                    rolls: list[int], time_col: str = TIME_COL) -> pd.DataFrame:
    """按分钟数添加滞后与滚动均值特征。"""
    df = df.copy().reset_index(drop=True)
    for lag in lags:
        df[f"lag_{lag}"] = df[target].shift(lag)
    for w in rolls:
        df[f"roll_{w}"] = df[target].shift(1).rolling(w, min_periods=1).mean()
    return df


def dropna_features(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    return df.dropna(subset=[c for c in feature_cols if c in df.columns]).reset_index(drop=True)


def running_mask(power_series: pd.Series, threshold: float = 1.0) -> pd.Series:
    return (pd.to_numeric(power_series, errors="coerce") > threshold)
