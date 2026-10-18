"""用气负荷预测特征工程。"""
from __future__ import annotations

import pandas as pd

from air_station.data import transformers as T

TIME_COL = "time"


def build_features(station: pd.DataFrame, config: dict) -> pd.DataFrame:
    """基于站级宽表构建监督学习特征。"""
    target = config.get("target", "总流量")
    fcfg = config.get("features", {})
    df = station.copy()
    df = T.add_calendar(df, TIME_COL)

    if fcfg.get("load_ratio", True) and "负荷率" in df.columns:
        df["load_ratio_feat"] = df["负荷率"]

    df = T.add_lag_rolling(df, target,
                           lags=fcfg.get("lags_minutes", [1, 5, 15, 60, 1440]),
                           rolls=fcfg.get("rolling_minutes", [15, 60, 240]))

    # 环境特征
    for c in fcfg.get("ambient", []):
        if c in df.columns:
            df[f"amb_{c}"] = df[c]

    df["target"] = df[target]
    return df


def feature_columns(df: pd.DataFrame) -> list[str]:
    cols = ["hour", "minute", "dow", "is_weekend"]
    cols += [c for c in df.columns if c.startswith(("lag_", "roll_", "amb_"))]
    cols += [c for c in ("load_ratio_feat",) if c in df.columns]
    return [c for c in cols if c in df.columns]
