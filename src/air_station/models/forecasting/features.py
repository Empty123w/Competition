"""用气负荷预测特征工程 (阶段 A3)。

严格防止未来信息泄漏:
  - 所有目标派生特征均基于 `target.shift(1)`(即 T-1 及以前);
  - 滚动统计在 shift(1) 之后计算, 窗口不含当前时刻;
  - 差分同样基于 shift(1);
  - 仅时间/周期/当前外生量(压力/环境/运行台数)使用 T 时刻值。

产出: data/processed/forecast_features.parquet
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from air_station.data import transformers as T

TIME_COL = "time"

# 特征字典(名称, 单位, 计算说明, 缺失处理)
FEATURE_SPECS: list[dict] = [
    {"name": "hour", "unit": "-", "desc": "小时(0-23)", "calc": "time.hour", "missing": "无缺失"},
    {"name": "weekday", "unit": "-", "desc": "星期(0=周一)", "calc": "time.dayofweek", "missing": "无缺失"},
    {"name": "month", "unit": "-", "desc": "月份", "calc": "time.month", "missing": "无缺失"},
    {"name": "is_workday", "unit": "-", "desc": "是否工作日", "calc": "weekday<5", "missing": "无缺失"},
    {"name": "hour_sin", "unit": "-", "desc": "小时周期sin", "calc": "sin(2π·hour/24)", "missing": "无缺失"},
    {"name": "hour_cos", "unit": "-", "desc": "小时周期cos", "calc": "cos(2π·hour/24)", "missing": "无缺失"},
    {"name": "weekday_sin", "unit": "-", "desc": "星期周期sin", "calc": "sin(2π·weekday/7)", "missing": "无缺失"},
    {"name": "weekday_cos", "unit": "-", "desc": "星期周期cos", "calc": "cos(2π·weekday/7)", "missing": "无缺失"},
    {"name": "lag_0", "unit": "m3/min", "desc": "当前观测总流量(T 时刻已知, 用于预测 T+h)", "calc": "target(t)", "missing": "保留NaN"},
    {"name": "lag_k", "unit": "m3/min", "desc": "k 分钟前总流量", "calc": "target.shift(k)", "missing": "保留NaN(树模型)"},
    {"name": "roll_w_mean", "unit": "m3/min", "desc": "过去 w 分钟均值", "calc": "target.shift(1).rolling(w)", "missing": "保留NaN"},
    {"name": "roll_w_std", "unit": "m3/min", "desc": "过去 w 分钟标准差", "calc": "同上.std", "missing": "保留NaN"},
    {"name": "roll_w_min", "unit": "m3/min", "desc": "过去 w 分钟最小", "calc": "同上.min", "missing": "保留NaN"},
    {"name": "roll_w_max", "unit": "m3/min", "desc": "过去 w 分钟最大", "calc": "同上.max", "missing": "保留NaN"},
    {"name": "diff_k", "unit": "m3/min", "desc": "k 分钟差分", "calc": "base - base.shift(k)", "missing": "保留NaN"},
    {"name": "ambient_temp_c", "unit": "℃", "desc": "站房温度", "calc": "station_temperature_c", "missing": "短缺口已插值"},
    {"name": "ambient_humidity_pct", "unit": "%RH", "desc": "站房湿度", "calc": "station_humidity_percent", "missing": "短缺口已插值"},
    {"name": "header_pressure_mpa", "unit": "MPa", "desc": "母管压力", "calc": "station.header_pressure_mpa", "missing": "保留NaN"},
    {"name": "running_count", "unit": "台", "desc": "运行机组数量", "calc": "device.running 按时间求和", "missing": "保留NaN"},
    {"name": "load_level", "unit": "-", "desc": "历史负荷等级", "calc": "station.load_level", "missing": "保留NaN"},
    {"name": "load_volatility_15", "unit": "-", "desc": "15min 波动率(变异系数)", "calc": "base.rolling(15).std/mean", "missing": "保留NaN"},
]


def build_features(station: pd.DataFrame, config: dict) -> pd.DataFrame:
    """基于站级宽表构建预测特征(无未来泄漏)。"""
    df = station.copy()
    t = pd.to_datetime(df[TIME_COL])
    df["hour"] = t.dt.hour
    df["weekday"] = t.dt.dayofweek
    df["month"] = t.dt.month
    df["is_workday"] = (t.dt.dayofweek < 5).astype(int)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24.0)
    df["weekday_sin"] = np.sin(2 * np.pi * df["weekday"] / 7.0)
    df["weekday_cos"] = np.cos(2 * np.pi * df["weekday"] / 7.0)

    fcfg = config.get("features", {})
    target_col = config.get("target", {}).get("column", "total_flow_m3_min")
    y = pd.to_numeric(df[target_col], errors="coerce")
    base = y.shift(1)   # 防泄漏: 滚动/差分仅用 T-1 及以前
    df["lag_0"] = y     # 当前观测值(T 时刻已知, 预测 T+h 时可用, 非未来信息)

    for lag in fcfg.get("lags_minutes", [1, 5, 15, 30, 60, 1440]):
        df[f"lag_{lag}"] = y.shift(lag)
    for w in fcfg.get("rolling_minutes", [5, 15, 30, 60]):
        r = base.rolling(w, min_periods=1)
        df[f"roll_{w}_mean"] = r.mean()
        df[f"roll_{w}_std"] = r.std()
        df[f"roll_{w}_min"] = r.min()
        df[f"roll_{w}_max"] = r.max()
    for d in fcfg.get("diff_minutes", [1, 5, 15]):
        df[f"diff_{d}"] = base - base.shift(d)

    if "station_temperature_c" in df.columns:
        df["ambient_temp_c"] = df["station_temperature_c"]
    if "station_humidity_percent" in df.columns:
        df["ambient_humidity_pct"] = df["station_humidity_percent"]
    r15 = base.rolling(15, min_periods=2)
    df["load_volatility_15"] = (r15.std() / r15.mean().replace(0, np.nan))

    df["target"] = y
    df.drop(columns=[target_col], inplace=True, errors="ignore")
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


EXCLUDE_DEFAULT = ["header_pressure_mpa", "total_power_kw", "load_rate",
                   "station_temperature_c", "station_humidity_percent",
                   "environment_interpolated"]


def feature_columns(df: pd.DataFrame, exclude: list[str] | None = None) -> list[str]:
    drop = {TIME_COL, "target", "environment_interpolated", "load_band", "device_id"}
    drop |= set(exclude if exclude is not None else EXCLUDE_DEFAULT)
    cols = []
    for c in df.columns:
        if c in drop:
            continue
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_bool_dtype(df[c]):
            cols.append(c)
    return cols


def build_forecast_features(processed_dir: str | Path = "data/processed",
                            config: dict | None = None) -> pd.DataFrame:
    """组装站级+设备级, 产出预测特征表(落盘)。"""
    import yaml
    cfg = config or yaml.safe_load(Path("configs/forecasting.yaml").read_text(encoding="utf-8"))
    station = T.load_processed(processed_dir, "station_timeseries")
    df = build_features(station, cfg)

    # 运行机组数量
    try:
        dev = T.load_processed(processed_dir, "device_timeseries")
        rc = dev.groupby(TIME_COL, as_index=False)["running"].sum().rename(
            columns={"running": "running_count"})
        df = df.merge(rc, on=TIME_COL, how="left")
    except FileNotFoundError:
        df["running_count"] = np.nan

    df = df.sort_values(TIME_COL).reset_index(drop=True)
    # 强制同上(防止偶然的列顺序差异): 幂等
    out = Path(processed_dir) / "forecast_features.parquet"
    df.to_parquet(out, index=False)
    return df


if __name__ == "__main__":
    d = build_forecast_features()
    print(f"[OK] forecast_features {d.shape}")
