"""RUL 模型特征工程。

特征全部来自设备可观测信号(振动/温度/电流/能效), **不使用真实 RUL 或时间进度**,
天然无未来信息泄漏。滚动统计按分组(训练用 episode_id, 服务用 device_id)计算。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["vib1_current", "vib2_current", "vib3_current", "discharge_temp_c",
            "oil_tank_c", "current_proxy_a", "unit_flow_power",
            "vib_roll", "temp_roll", "vib_delta"]


def build_features(df: pd.DataFrame, group_col: str = "episode_id",
                   window_steps: int = 8) -> pd.DataFrame:
    df = df.sort_values([group_col, "time"]).copy()
    vib_mean = df[["vib1_current", "vib2_current", "vib3_current"]].mean(axis=1)
    df["_vib_mean"] = vib_mean
    g = df.groupby(group_col, observed=True)
    df["vib_roll"] = g["_vib_mean"].transform(lambda s: s.rolling(window_steps, min_periods=1).mean())
    df["temp_roll"] = g["discharge_temp_c"].transform(
        lambda s: s.rolling(window_steps, min_periods=1).mean())
    df["vib_delta"] = g["_vib_mean"].transform(lambda s: s.diff().fillna(0.0))
    df = df.drop(columns=["_vib_mean"])
    return df


def feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return df[FEATURES].astype("float64")
