"""设备健康诊断特征工程。"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from air_station.data import transformers as T

TIME_COL = "time"
DEVICE_PREFIX = re.compile(r"^([45])#(.+)$")


def _split_columns(df: pd.DataFrame) -> dict[int, dict[str, str]]:
    """把 `4#/5#` 前缀列按设备分组: {device: {indicator: column}}。"""
    out: dict[int, dict[str, str]] = {}
    for c in df.columns:
        m = DEVICE_PREFIX.match(str(c))
        if m:
            out.setdefault(int(m.group(1)), {})[m.group(2)] = c
    return out


def device_monitor_frame(processed_dir: str, number: int,
                         rule: str = "1h") -> pd.DataFrame:
    """返回某机组的小时级状态监测宽表(仅该设备指标)。"""
    df = T.load_processed(processed_dir, "设备状态监测")
    groups = _split_columns(df)
    mapping = groups.get(number, {})
    sub = df[[TIME_COL] + list(mapping.values())].copy()
    sub = sub.rename(columns={v: k for k, v in mapping.items()})
    sub = T.resample(sub, rule, agg="mean")
    return sub


def add_trend_features(df: pd.DataFrame, indicators: list[str],
                       windows: tuple[int, ...] = (6, 24)) -> pd.DataFrame:
    """为指标添加滚动均值与变化率特征(按小时粒度)。"""
    out = df.copy()
    for ind in indicators:
        if ind not in out.columns:
            continue
        s = pd.to_numeric(out[ind], errors="coerce")
        for w in windows:
            out[f"{ind}__roll{w}"] = s.rolling(w, min_periods=1).mean()
        out[f"{ind}__delta"] = s.diff().fillna(0.0)
    return out


def build_device_features(processed_dir: str, number: int, config: dict) -> pd.DataFrame:
    """构建单机健康特征表(小时级)。"""
    rule = f"{config.get('granularity_minutes', 60)}min"
    inds = []
    for v in config.get("indicators", {}).values():
        inds.extend(v)
    frame = device_monitor_frame(processed_dir, number, rule=rule)
    frame = add_trend_features(frame, inds)
    return frame


def value_frame(processed_dir: str, number: int, config: dict) -> pd.DataFrame:
    """仅返回原始指标(小时级), 供规则与异常检测使用。"""
    rule = f"{config.get('granularity_minutes', 60)}min"
    return device_monitor_frame(processed_dir, number, rule=rule)
