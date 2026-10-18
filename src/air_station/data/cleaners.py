"""数据清洗: 宽表转长表、无效列剔除、哨兵处理、运行态掩码。

产出两种规范表:
  - long_df:  逐设备指标长表 [time, device_number, indicator, category, value]
  - station_df: 站级宽表 [time, 母管压力, 总流量, 负荷率, 负荷等级, 站房温度, 站房湿度]
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

TIME_COL = "time"
DEVICE_PREFIX = re.compile(r"^([45])#(.+)$")

# 指标大类映射（用于长表标注）
CATEGORY_MAP = {
    "排气压力": "运行参数", "实时流量": "运行参数", "A相电流": "运行参数",
    "C相电流": "运行参数", "功率": "运行参数", "IGV开度": "运行参数",
    "BOV闭度": "运行参数", "变速箱油压": "运行参数", "油滤器压差": "运行参数",
    "空气过滤器压降": "运行参数", "震动": "状态监测", "温度": "状态监测",
}


def _category(indicator: str) -> str:
    for key, cat in CATEGORY_MAP.items():
        if key in indicator:
            return cat
    return "其他"


def load_config(path: str | Path = "configs/data_quality.yaml") -> dict:
    p = Path(path)
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def to_long(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """把含 `4#/5#` 前缀的宽表转成长表, 按来源表分别返回以控制内存。"""
    out: dict[str, pd.DataFrame] = {}
    for name, df in tables.items():
        if TIME_COL not in df.columns:
            continue
        cols = [c for c in df.columns if DEVICE_PREFIX.match(str(c))]
        if not cols:
            continue
        sub = df[[TIME_COL] + cols].melt(id_vars=TIME_COL, var_name="col", value_name="value")
        parsed = sub["col"].str.extract(DEVICE_PREFIX.pattern)
        sub["device_number"] = parsed[0].astype("int8")
        sub["indicator"] = parsed[1].astype("category")
        sub["category"] = sub["indicator"].astype(str).map(_category).astype("category")
        sub["value"] = pd.to_numeric(sub["value"], errors="coerce").astype("float32")
        out[name] = sub[[TIME_COL, "device_number", "indicator", "category", "value"]]
        del sub
    return out


def build_station(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """构建站级宽表。"""
    base_src = tables.get("用气负荷数据")
    if base_src is None:
        base_src = tables.get("设备运行参数")
    out = pd.DataFrame({TIME_COL: base_src[TIME_COL]})
    load = tables.get("用气负荷数据")
    net = tables.get("管网运行数据")
    env = tables.get("环境参数")
    if load is not None:
        out["总流量"] = load.get("总流量")
        out["负荷率"] = load.get("负荷率")
        out["负荷等级"] = load.get("负荷等级")
        out["波动率_15min"] = load.get("波动率_15min")
    if net is not None:
        out["母管压力"] = net.get("母管压力")
    if env is not None:
        out["站房温度"] = env.get("站房温度")
        out["站房湿度"] = env.get("站房湿度")
    return out


def drop_invalid(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    keep = [c for c in df.columns if c not in set(columns)]
    return df[keep].copy()


def mask_sentinels(df: pd.DataFrame, sentinels: dict) -> pd.DataFrame:
    """把哨兵值替换为 NaN。"""
    df = df.copy()
    keys = {float(k) for k in (sentinels or {}).keys()}
    for c in df.columns:
        if c == TIME_COL:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            df.loc[df[c].isin(keys), c] = np.nan
    return df


def fix_environment_zeros(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """把环境参数 0 掉点视为缺失并前值填充。"""
    df = df.copy()
    for c in columns:
        if c in df.columns:
            df.loc[df[c] == 0, c] = np.nan
            df[c] = df[c].ffill().bfill()
    return df


def add_running_flags(op: pd.DataFrame, threshold_kw: float = 1.0) -> pd.DataFrame:
    """为运行参数表增加 4#/5# 运行标志。"""
    df = op.copy()
    for u in ("4#", "5#"):
        col = f"{u}功率"
        if col in df.columns:
            df[f"{u}运行"] = (pd.to_numeric(df[col], errors="coerce") > threshold_kw).astype(int)
    return df


def clean_all(tables: dict[str, pd.DataFrame], config: dict | None = None) -> dict[str, pd.DataFrame]:
    """执行清洗 (不含长表), 返回 {各清洗后表, station}。"""
    config = config or {}
    drop_cols = config.get("drop_columns", [])
    sentinels = config.get("sentinel_values", {})
    env_cols = config.get("environment_zero_fill", {}).get("columns", ["站房温度", "站房湿度"])
    thr = config.get("running_power_threshold_kw", 1.0)

    cleaned: dict[str, pd.DataFrame] = {}
    for name, df in tables.items():
        d = drop_invalid(df, drop_cols)
        d = mask_sentinels(d, sentinels)
        if name == "环境参数":
            d = fix_environment_zeros(d, env_cols)
        if name == "设备运行参数":
            d = add_running_flags(d, thr)
        cleaned[name] = d

    cleaned["station"] = build_station(cleaned)
    return cleaned


def persist_long(longs: dict[str, pd.DataFrame], processed_dir: str | Path = "data/processed") -> list[Path]:
    out_dir = Path(processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sub_name, sub_df in longs.items():
        path = out_dir / f"long_{sub_name}.parquet"
        _write_frame(sub_df, path)
        written.append(path)
    return written


def persist(cleaned: dict, processed_dir: str | Path = "data/processed") -> list[Path]:
    out_dir = Path(processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in cleaned.items():
        if isinstance(df, dict):
            # 长表: 按来源表分别落盘, 控制单文件与内存
            for sub_name, sub_df in df.items():
                path = out_dir / f"long_{sub_name}.parquet"
                _write_frame(sub_df, path)
                written.append(path)
            continue
        path = out_dir / f"{name}.parquet"
        _write_frame(df, path)
        written.append(path)
    return written


def _write_frame(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False)
    except Exception:
        df.to_csv(path.with_suffix(".csv"), index=False)
