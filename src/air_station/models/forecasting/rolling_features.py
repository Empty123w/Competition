"""滚动回放: 因果特征构建 (阶段 A6)。

在**原生 1 分钟时间轴**上计算滞后/滚动/差分, 再抽取 5 分钟样本;
环境温湿度采用**因果前向填充**(仅用过去值, 最长由配置限制);
不读取当前 forecast_features.parquet 中的环境值。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TIME = "time"


def load_config(path: str = "configs/rolling_forecast.yaml") -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _causal_environment(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    """把 environment_interpolated=True 的行恢复为空值, 只做前向填充。"""
    for col in ("station_temperature_c", "station_humidity_percent"):
        if col not in df.columns:
            continue
        if "environment_interpolated" in df.columns:
            df.loc[df["environment_interpolated"].astype(bool), col] = np.nan
    df["ambient_temp_c"] = df["station_temperature_c"].ffill(limit=limit)
    df["ambient_humidity_pct"] = df["station_humidity_percent"].ffill(limit=limit)
    return df


def build_features(processed_dir: str = "data/processed",
                   config: dict | None = None) -> pd.DataFrame:
    cfg = config or load_config()
    fcfg = cfg["features"]
    limit = int(fcfg.get("causal_fill_limit_minutes", 30))
    tcol = cfg["target"]["column"]

    st = pd.read_parquet(Path(processed_dir) / "station_timeseries.parquet")
    st[TIME] = pd.to_datetime(st[TIME])
    st = st.sort_values(TIME).reset_index(drop=True)
    st = _causal_environment(st, limit)

    try:
        dev = pd.read_parquet(Path(processed_dir) / "device_timeseries.parquet",
                              columns=[TIME, "running"])
        rc = dev.groupby(TIME, as_index=False)["running"].sum().rename(
            columns={"running": "running_count"})
        st = st.merge(rc, on=TIME, how="left")
    except FileNotFoundError:
        st["running_count"] = np.nan

    y = pd.to_numeric(st[tcol], errors="coerce")
    df = pd.DataFrame({TIME: st[TIME]})
    df["target"] = y.to_numpy()
    df["lag_0"] = y.to_numpy()
    for k in fcfg["lags_minutes"]:
        df[f"lag_{k}"] = y.shift(k).to_numpy()
    for w in fcfg["rolling_minutes"]:
        df[f"roll_{w}_mean"] = y.rolling(w, min_periods=1).mean().to_numpy()
    for k in fcfg["diff_minutes"]:
        df[f"diff_{k}"] = (y - y.shift(k)).to_numpy()

    t = df[TIME]
    df["hour"] = t.dt.hour; df["weekday"] = t.dt.dayofweek
    df["month"] = t.dt.month; df["is_workday"] = (t.dt.dayofweek < 5).astype(int)
    df["hour_sin"] = np.sin(2*np.pi*df["hour"]/24.0); df["hour_cos"] = np.cos(2*np.pi*df["hour"]/24.0)
    df["weekday_sin"] = np.sin(2*np.pi*df["weekday"]/7.0); df["weekday_cos"] = np.cos(2*np.pi*df["weekday"]/7.0)

    df["ambient_temp_c"] = st.get("ambient_temp_c")
    df["ambient_humidity_pct"] = st.get("ambient_humidity_pct")
    df["running_count"] = st.get("running_count")
    df["load_level"] = st.get("load_level")

    df = df.replace([np.inf, -np.inf], np.nan)
    return df


def sample_grid(df: pd.DataFrame, issue_frequency_minutes: int = 5) -> pd.DataFrame:
    """在 1 分钟特征上抽取满足 minute % step == 0 的行。"""
    m = df[TIME].dt.minute % issue_frequency_minutes == 0
    return df[m].reset_index(drop=True)


def feature_columns(profile: str, cfg: dict) -> list[str]:
    drop = {"time", "target"}
    if profile == "core_no_environment":
        drop |= set(cfg["features"].get("core_no_environment_drop", []))
    cols = [c for c in _all_columns(cfg) if c not in drop]
    return cols


def _all_columns(cfg: dict) -> list[str]:
    fcfg = cfg["features"]
    cols = ["lag_0"] + [f"lag_{k}" for k in fcfg["lags_minutes"]]
    cols += [f"roll_{w}_mean" for w in fcfg["rolling_minutes"]]
    cols += [f"diff_{k}" for k in fcfg["diff_minutes"]]
    cols += fcfg["calendar"] + fcfg["cycle"]
    if fcfg.get("include_running_count", True):
        cols.append("running_count")
    if fcfg.get("include_load_level", True):
        cols.append("load_level")
    cols += ["ambient_temp_c", "ambient_humidity_pct"]
    return cols


def environment_missing_rate(df: pd.DataFrame) -> tuple[float, float]:
    def rate(c):
        return float(df[c].isna().mean()) if c in df.columns and len(df) else 0.0
    return rate("ambient_temp_c"), rate("ambient_humidity_pct")
