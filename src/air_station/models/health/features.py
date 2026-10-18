"""设备健康诊断特征工程 (阶段 A3)。

要点:
  - 按设备分组计算滞后/滚动/趋势, 严格防止跨设备与未来信息泄漏;
  - 按运行工况建模: 停机行不参与基准拟合, 相对偏差置 NaN(不填 0);
  - 按加载率分层(低/中/高)建立参考分布, 避免正常启停双峰被误判;
  - 所有除法做除零保护, 结果统一将 inf 置 NaN。

产出: data/processed/health_features.parquet
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from air_station.data import transformers as T

TIME_COL = "time"
EPS = 0.1  # 除零保护阈值

FEATURE_SPECS: list[dict] = [
    {"name": "vib{s}_current", "unit": "mm/s", "desc": "第 s 级振动当前值", "calc": "raw", "missing": "保留NaN"},
    {"name": "vib{s}_mean60", "unit": "mm/s", "desc": "第 s 级振动过去60min均值", "calc": "shift(1).rolling(60).mean", "missing": "保留NaN"},
    {"name": "vib{s}_max60", "unit": "mm/s", "desc": "第 s 级振动过去60min最大", "calc": "shift(1).rolling(60).max", "missing": "保留NaN"},
    {"name": "vib{s}_std60", "unit": "mm/s", "desc": "第 s 级振动过去60min标准差", "calc": "shift(1).rolling(60).std", "missing": "保留NaN"},
    {"name": "vib{s}_delta", "unit": "mm/s", "desc": "第 s 级振动1min变化率", "calc": "shift(1)-shift(2)", "missing": "保留NaN"},
    {"name": "bearing_d_nd_max_diff_c", "unit": "℃", "desc": "驱动/非驱动端轴承温差", "calc": "|D-ND|", "missing": "保留NaN"},
    {"name": "coil_max_diff_c", "unit": "℃", "desc": "电机三相线圈最大温差", "calc": "max(U,V,W)-min(U,V,W)", "missing": "保留NaN"},
    {"name": "unit_flow_power", "unit": "kW/(m3/min)", "desc": "单位流量功率", "calc": "power/flow (flow>eps)", "missing": "分母过小置NaN"},
    {"name": "trend{N}_{ind}", "unit": "指标单位/min", "desc": "{ind} 过去 N 分钟趋势斜率", "calc": "(base-base.shift(N))/N", "missing": "保留NaN"},
    {"name": "dev_{ind}", "unit": "-", "desc": "相对同负荷区间正常基准的偏差(z)", "calc": "(x-band_mean)/band_std", "missing": "停机置NaN"},
]

# 关键指标(当前值列名 -> 源)
VIB = {1: "一级震动", 2: "二级震动", 3: "三级震动"}
TEMP_EXTRA = {
    "bearing_d": "电机轴承D端温度", "bearing_nd": "电机轴承ND端温度",
    "coil_u": "电机线圈1U1温度", "coil_v": "电机线圈1V1温度", "coil_w": "电机线圈1W1温度",
    "gearbox_oil": "齿轮箱供油温度", "oil_tank": "油箱油温", "cooling_water": "冷却水温度",
}
PRESS_EXTRA = {"oil_filter_dp": "油滤器压差", "air_filter_dp": "空气过滤器压降",
               "gearbox_oil_pressure": "变速箱油压"}
TREND_INDICATORS = ["vib1_current", "discharge_temp_c", "bearing_d_c", "oil_tank_c",
                    "current_proxy_a", "power_kw"]
TREND_WINDOWS = [15, 60, 240]
DEV_INDICATORS = ["vib1_current", "vib2_current", "vib3_current", "discharge_temp_c",
                  "bearing_d_c", "oil_tank_c", "current_proxy_a", "unit_flow_power"]


def _read_f32_usecols(path: Path, usecols: list[str], encodings=("utf-8-sig", "gbk")):
    for enc in encodings:
        try:
            return pd.read_csv(path, encoding=enc, usecols=usecols, dtype="float32",
                               parse_dates=[TIME_COL], low_memory=True)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"读取失败 {path}")


def _guard_div(num: pd.Series, den: pd.Series, eps: float = EPS) -> pd.Series:
    d = den.astype("float64")
    out = num.astype("float64") / d.where(d.abs() > eps)
    return out


UNIT_FLOW_MIN_FLOW = 10.0  # m3/min, 小于该流量时单位流量功率无意义


def _load_band(load_ratio: pd.Series, bands: dict) -> pd.Series:
    lo = bands.get("low_upper", 0.3)
    hi = bands.get("high_lower", 0.7)
    band = pd.Series("mid", index=load_ratio.index, dtype="object")
    band[load_ratio < lo] = "low"
    band[load_ratio > hi] = "high"
    band[load_ratio.isna()] = "na"
    return band.astype("category")


def _device_frame(d: int, dev: pd.DataFrame, st: pd.DataFrame, op: pd.DataFrame,
                  cfg: dict) -> pd.DataFrame:
    p = f"{d}#"
    sub = dev[dev["device_id"] == d].sort_values(TIME_COL).reset_index(drop=True)
    f = pd.DataFrame({TIME_COL: sub[TIME_COL]})
    f["device_id"] = np.int8(d)
    f["running"] = sub["running"].values
    f["load_ratio"] = sub["load_ratio"].astype("float64").values
    f["power_kw"] = sub["power_kw"].astype("float64").values
    f["flow_m3_min"] = sub["flow_m3_min"].astype("float64").values
    f["current_proxy_a"] = sub["current_proxy_a"].astype("float64").values
    f["discharge_temp_c"] = sub["discharge_temperature_c"].astype("float64").values

    # 振动
    for s, name in VIB.items():
        v = sub[f"vibration_stage_{s}_mm_s"].astype("float64")
        base = v.shift(1)
        f[f"vib{s}_current"] = v.values
        r = base.rolling(60, min_periods=1)
        f[f"vib{s}_mean60"] = r.mean().values
        f[f"vib{s}_max60"] = r.max().values
        f[f"vib{s}_std60"] = r.std().values
        f[f"vib{s}_delta"] = (base - base.shift(1)).values

    # 额外温度 (来自原始状态监测)
    for key, col in TEMP_EXTRA.items():
        if f"{p}{col}" in st.columns:
            f[f"{key}_c"] = st[f"{p}{col}"].astype("float64").values
    # 额外压力 (来自原始运行参数)
    for key, col in PRESS_EXTRA.items():
        if f"{p}{col}" in op.columns:
            f[f"{key}"] = op[f"{p}{col}"].astype("float64").values

    if {"coil_u_c", "coil_v_c", "coil_w_c"}.issubset(f.columns):
        f["coil_max_diff_c"] = (f[["coil_u_c", "coil_v_c", "coil_w_c"]].max(axis=1)
                                - f[["coil_u_c", "coil_v_c", "coil_w_c"]].min(axis=1))
    if {"bearing_d_c", "bearing_nd_c"}.issubset(f.columns):
        f["bearing_d_nd_max_diff_c"] = (f["bearing_d_c"] - f["bearing_nd_c"]).abs()
    f["unit_flow_power"] = _guard_div(f["power_kw"], f["flow_m3_min"], UNIT_FLOW_MIN_FLOW)

    # 趋势斜率(首尾差分, 基于 shift(1) 防泄漏)
    for ind in TREND_INDICATORS:
        if ind not in f.columns:
            continue
        base = f[ind].shift(1)
        for n in TREND_WINDOWS:
            f[f"trend{n}_{ind}"] = ((base - base.shift(n)) / n).values
    return f


def build_health_features(raw_dir: str | Path = "data",
                          processed_dir: str | Path = "data/processed",
                          config: dict | None = None) -> pd.DataFrame:
    import yaml
    cfg = config or yaml.safe_load(Path("configs/health.yaml").read_text(encoding="utf-8"))
    raw_dir = Path(raw_dir)
    bands = cfg.get("load_bands", {"low_upper": 0.3, "high_lower": 0.7})
    base_range = cfg.get("baseline_range", {})

    dev = T.load_processed(processed_dir, "device_timeseries")
    st_cols = [TIME_COL] + [f"{d}#{c}" for d in (4, 5) for c in TEMP_EXTRA.values()]
    op_cols = [TIME_COL] + [f"{d}#{c}" for d in (4, 5) for c in PRESS_EXTRA.values()]
    st = _read_f32_usecols(raw_dir / "设备状态监测.csv", st_cols)
    op = _read_f32_usecols(raw_dir / "设备运行参数.csv", op_cols)

    frames = [_device_frame(d, dev, st, op, cfg) for d in (4, 5)]
    f = pd.concat(frames, ignore_index=True)
    f["load_band"] = _load_band(f["load_ratio"], bands)

    # 分层基准: 仅运行且处于基线期
    base_start = pd.Timestamp(base_range.get("start", "1900-01-01"))
    base_end = pd.Timestamp(base_range.get("end", "2100-01-01"))
    base_mask = f["running"] & f[TIME_COL].between(base_start, base_end)
    key = pd.MultiIndex.from_frame(f[["device_id", "load_band"]])
    running_vals = f["running"].values
    for ind in DEV_INDICATORS:
        if ind not in f.columns:
            continue
        stats = (f[base_mask].groupby(["device_id", "load_band"], observed=True)[ind]
                 .agg(["mean", "std"]))
        m = stats["mean"].reindex(key).to_numpy()
        s = stats["std"].reindex(key).to_numpy()
        s = np.where(np.abs(s) > 1e-9, s, np.nan)
        dev_col = (f[ind].to_numpy() - m) / s
        dev_col = np.where(running_vals, dev_col, np.nan)   # 停机置 NaN, 不填 0
        f[f"dev_{ind}"] = dev_col

    f = f.replace([np.inf, -np.inf], np.nan)
    f = f.sort_values([TIME_COL, "device_id"]).reset_index(drop=True)
    Path(processed_dir).mkdir(parents=True, exist_ok=True)
    f.to_parquet(Path(processed_dir) / "health_features.parquet", index=False)
    return f


if __name__ == "__main__":
    d = build_health_features()
    print(f"[OK] health_features {d.shape}")
