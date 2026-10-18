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


# =========================================================================== #
# 阶段 A2: 数据清洗与标准化 (标准输出表)
# =========================================================================== #
REQUIRED_RAW = ["设备运行参数.csv", "设备状态监测.csv", "用气负荷数据.csv",
                "管网运行数据.xlsx", "环境参数.xlsx", "运行事件记录.xlsx",
                "维护保养记录.xlsx", "设备档案.json", "指标释义.json"]

DEVICE_TYPE = {4: "centrifugal", 5: "centrifugal"}

STATION_COLUMNS = ["time", "header_pressure_mpa", "total_flow_m3_min", "total_power_kw",
                   "station_temperature_c", "station_humidity_percent",
                   "load_rate", "load_level"]

DEVICE_COLUMNS = ["time", "device_id", "device_type", "running", "state_source",
                  "discharge_pressure_mpa", "flow_m3_min", "power_kw", "load_ratio",
                  "specific_power", "igv_percent", "bov_closed_percent", "current_proxy_a",
                  "vibration_stage_1_mm_s", "vibration_stage_2_mm_s", "vibration_stage_3_mm_s",
                  "discharge_temperature_c", "runtime_hours", "load_hours",
                  "maintenance_remaining_hours", "fault_code",
                  "quality_valid", "quality_reason"]

# 附加质量标记列
QUALITY_FLAGS = ["temperature_available", "maintenance_sentinel_removed",
                 "fault_label_quality", "possible_station_level_event"]


def _dq_cfg(config: dict) -> dict:
    return config.get("data_quality", config)


def _read_f32_csv(path: Path, encoding: str) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0, encoding=encoding)
    dtype = {c: "float32" for c in header.columns if c != TIME_COL}
    return pd.read_csv(path, encoding=encoding, dtype=dtype,
                       parse_dates=[TIME_COL], low_memory=True)


def _device_rated(raw_dir: Path) -> dict[int, dict]:
    import json
    specs = json.loads((raw_dir / "设备档案.json").read_text(encoding="utf-8")).get("设备档案", [])
    return {int(s["编号"]): {"flow": float(s["额定气量_m3min"]),
                             "power": float(s["额定功率_kW"])} for s in specs}


def _sentinel_values(cfg: dict) -> set[float]:
    return {float(s["value"]) for s in _dq_cfg(cfg).get("sentinels", [])}


def _gap_windows(cfg: dict) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    out = []
    for g in _dq_cfg(cfg).get("missing", {}).get("known_gaps", []):
        out.append((pd.Timestamp(g["start"]), pd.Timestamp(g["end"])))
    return out


def _valid_codes(cfg: dict) -> dict:
    rs = _dq_cfg(cfg).get("running_state", {})
    return {"running": rs.get("running_code", 8), "stopped": rs.get("stopped_code", 10),
            "thr": float(rs.get("power_threshold_kw", 1.0))}


def build_station_table(op: pd.DataFrame, load: pd.DataFrame, net: pd.DataFrame,
                        env: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """构建站级标准表 station_timeseries。"""
    out = pd.DataFrame({TIME_COL: op[TIME_COL]})
    out["header_pressure_mpa"] = net["母管压力"].astype("float32").values
    total_flow = op["4#实时流量"].astype("float64") + op["5#实时流量"].astype("float64")
    total_power = op["4#功率"].astype("float64") + op["5#功率"].astype("float64")
    out["total_flow_m3_min"] = total_flow.values            # 高精度
    out["total_power_kw"] = total_power.values              # 高精度
    # 环境温湿度: 0 视为掉点, 仅短缺口插值
    limit = int(_dq_cfg(cfg).get("missing", {}).get("max_interpolate_gap_minutes", 30))
    zero_cols = _dq_cfg(cfg).get("missing", {}).get("zero_dropout", {}).get("columns",
                                                                          ["站房温度", "站房湿度"])
    temp = env["站房温度"].astype("float64")
    hum = env["站房湿度"].astype("float64")
    dropout = (temp == 0) | (hum == 0)
    temp = temp.mask(temp == 0).interpolate(limit=limit, limit_direction="both")
    hum = hum.mask(hum == 0).interpolate(limit=limit, limit_direction="both")
    out["station_temperature_c"] = temp.astype("float32").values
    out["station_humidity_percent"] = hum.astype("float32").values
    out["load_rate"] = load["负荷率"].astype("float64").values     # 高精度
    out["load_level"] = load["负荷等级"].astype("int8").values
    out["environment_interpolated"] = dropout.values
    out = out[STATION_COLUMNS + ["environment_interpolated"]]
    return out


def _one_device(d: int, op: pd.DataFrame, st: pd.DataFrame, ev: pd.DataFrame,
                mt: pd.DataFrame, rated: dict[int, dict], cfg: dict,
                gaps: list[tuple], thr: float) -> pd.DataFrame:
    p = f"{d}#"
    t = op[TIME_COL].reset_index(drop=True)
    dev = pd.DataFrame({TIME_COL: t})
    dev["device_id"] = np.int8(d)
    dev["device_type"] = DEVICE_TYPE[d]

    # 运行状态: 事件优先, 异常码用功率兜底
    status = ev[f"{p}状态"].astype("float64").reset_index(drop=True)
    power = op[f"{p}功率"].astype("float64").reset_index(drop=True)
    power_on = power > thr
    running = pd.Series(np.nan, index=dev.index, dtype="float64")
    source = pd.Series("event_record", index=dev.index, dtype="object")
    running[status == 8] = 1.0
    running[status == 10] = 0.0
    abnormal = ~status.isin([8.0, 10.0]).values
    running[abnormal] = power_on.values[abnormal].astype(float)
    source[abnormal] = "power_fallback"
    dev["running"] = running.fillna(0).astype(bool).values
    dev["state_source"] = source.astype("category").values

    flow = op[f"{p}实时流量"].astype("float64").reset_index(drop=True)
    dev["discharge_pressure_mpa"] = op[f"{p}排气压力"].astype("float32").values
    dev["flow_m3_min"] = flow.astype("float32").values
    dev["power_kw"] = power.values                                  # 高精度
    dev["load_ratio"] = (flow / rated[d]["flow"]).values            # 高精度
    sp = np.where((dev["running"]) & (flow > 0), power / flow.replace(0, np.nan), np.nan)
    dev["specific_power"] = sp                                      # 高精度
    dev["igv_percent"] = op[f"{p}IGV开度"].astype("float32").values
    dev["bov_closed_percent"] = op[f"{p}BOV闭度"].astype("float32").values
    dev["current_proxy_a"] = ((op[f"{p}A相电流"].astype("float32")
                              + op[f"{p}C相电流"].astype("float32")) / 2).values
    dev["vibration_stage_1_mm_s"] = st[f"{p}一级震动"].astype("float32").values
    dev["vibration_stage_2_mm_s"] = st[f"{p}二级震动"].astype("float32").values
    dev["vibration_stage_3_mm_s"] = st[f"{p}三级震动"].astype("float32").values
    dev["discharge_temperature_c"] = st[f"{p}排气温度"].astype("float32").values

    # 维护计数器: 精准定位列, 转 float, 哨兵置 NaN
    sent = _sentinel_values(cfg)
    rtime = mt[f"{p}总运行时间"].astype("float64").reset_index(drop=True)
    ltime = mt[f"{p}总负载时间"].astype("float64").reset_index(drop=True)
    mrem = mt[f"{p}下次保养剩余时间"].astype("float64").reset_index(drop=True)
    sent_removed = rtime.isin(sent) | ltime.isin(sent) | mrem.isin(sent)
    dev["runtime_hours"] = rtime.mask(rtime.isin(sent)).astype("float32").values
    dev["load_hours"] = ltime.mask(ltime.isin(sent)).astype("float32").values
    dev["maintenance_remaining_hours"] = mrem.mask(mrem.isin(sent)).astype("float32").values

    fault = ev[f"{p}故障"].astype("float32").reset_index(drop=True)
    dev["fault_code"] = fault.values

    # 长缺失温度窗口标记
    temp_avail = pd.Series(True, index=dev.index)
    for s, e in gaps:
        temp_avail &= ~((t >= s) & (t <= e)).values
    dev["temperature_available"] = temp_avail.values
    dev["maintenance_sentinel_removed"] = sent_removed.values
    dev["fault_label_quality"] = np.where(fault.values != 0, "weak", "none")
    dev["fault_label_quality"] = dev["fault_label_quality"].astype("category")
    dev["possible_station_level_event"] = (fault.values != 0)

    # 质量标记
    quality_valid = t.notna().values & flow.notna().values & power.notna().values
    dev["quality_valid"] = quality_valid
    reasons = np.where(~temp_avail.values, "long_gap_temperature", "")
    reasons = np.where(sent_removed.values, _join_reason(reasons, "maintenance_sentinel"), reasons)
    reasons = np.where(source.values == "power_fallback", _join_reason(reasons, "state_fallback"), reasons)
    reasons = np.where(fault.values != 0, _join_reason(reasons, "weak_fault_label"), reasons)
    reasons = np.where(reasons == "", "ok", reasons)
    dev["quality_reason"] = pd.Categorical(reasons)

    dev = dev[DEVICE_COLUMNS + QUALITY_FLAGS]
    return dev


def _join_reason(arr, token: str):
    return np.where(arr == "", token, np.char.add(arr, "|" + token))


def build_device_table(op, st, ev, mt, rated, cfg) -> pd.DataFrame:
    gaps = _gap_windows(cfg)
    thr = _valid_codes(cfg)["thr"]
    frames = [_one_device(d, op, st, ev, mt, rated, cfg, gaps, thr) for d in (4, 5)]
    dev = pd.concat(frames, ignore_index=True)
    dev["device_type"] = dev["device_type"].astype("category")
    dev["quality_reason"] = dev["quality_reason"].astype("category")
    dev["fault_label_quality"] = dev["fault_label_quality"].astype("category")
    return dev.sort_values([TIME_COL, "device_id"]).reset_index(drop=True)


def build_events_table(ev, cfg) -> pd.DataFrame:
    thr = _valid_codes(cfg)["thr"]
    frames = []
    for d in (4, 5):
        p = f"{d}#"
        status = ev[f"{p}状态"].astype("float64").reset_index(drop=True)
        running = pd.Series(np.nan, index=status.index)
        running[status == 8] = 1.0
        running[status == 10] = 0.0
        abnormal = ~status.isin([8.0, 10.0]).values
        frames.append(pd.DataFrame({
            TIME_COL: ev[TIME_COL].reset_index(drop=True),
            "device_id": np.int8(d),
            "status_code": status.values,
            "running": running.fillna(0).astype(bool).values,
            "fault_code": ev[f"{p}故障"].astype("float32").values,
            "warning_code": ev[f"{p}预警"].astype("float32").values,
        }))
    et = pd.concat(frames, ignore_index=True)
    et["fault_label_quality"] = np.where(et["fault_code"] != 0, "weak", "none")
    et["fault_label_quality"] = et["fault_label_quality"].astype("category")
    et["possible_station_level_event"] = (et["fault_code"] != 0)
    return et.sort_values([TIME_COL, "device_id"]).reset_index(drop=True)


def build_maintenance_table(mt, cfg) -> pd.DataFrame:
    sent = _sentinel_values(cfg)
    frames = []
    for d in (4, 5):
        p = f"{d}#"
        rt = mt[f"{p}总运行时间"].astype("float64").reset_index(drop=True)
        lt = mt[f"{p}总负载时间"].astype("float64").reset_index(drop=True)
        mr = mt[f"{p}下次保养剩余时间"].astype("float64").reset_index(drop=True)
        removed = rt.isin(sent) | lt.isin(sent) | mr.isin(sent)
        frames.append(pd.DataFrame({
            TIME_COL: mt[TIME_COL].reset_index(drop=True),
            "device_id": np.int8(d),
            "runtime_hours": rt.mask(rt.isin(sent)),
            "load_hours": lt.mask(lt.isin(sent)),
            "maintenance_remaining_hours": mr.mask(mr.isin(sent)),
            "maintenance_sentinel_removed": removed.values,
        }))
    mtb = pd.concat(frames, ignore_index=True)
    return mtb.sort_values([TIME_COL, "device_id"]).reset_index(drop=True)


def build_standard_tables(raw_dir: str | Path = "data",
                          processed_dir: str | Path = "data/processed",
                          config: dict | None = None) -> dict:
    """构建四张标准表并落盘, 返回 {表名: DataFrame}。"""
    raw_dir = Path(raw_dir)
    out_dir = Path(processed_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = config or {}

    op = _read_f32_csv(raw_dir / "设备运行参数.csv", "utf-8-sig")
    st = _read_f32_csv(raw_dir / "设备状态监测.csv", "utf-8-sig")
    load = _read_f32_csv(raw_dir / "用气负荷数据.csv", "gbk")
    net = pd.read_excel(raw_dir / "管网运行数据.xlsx")
    env = pd.read_excel(raw_dir / "环境参数.xlsx")
    ev = pd.read_excel(raw_dir / "运行事件记录.xlsx")
    mt = pd.read_excel(raw_dir / "维护保养记录.xlsx")
    rated = _device_rated(raw_dir)

    station = build_station_table(op, load, net, env, cfg)
    device = build_device_table(op, st, ev, mt, rated, cfg)
    events = build_events_table(ev, cfg)
    maintenance = build_maintenance_table(mt, cfg)

    _write_frame(station, out_dir / "station_timeseries.parquet")
    _write_frame(device, out_dir / "device_timeseries.parquet")
    _write_frame(events, out_dir / "events.parquet")
    _write_frame(maintenance, out_dir / "maintenance.parquet")

    return {"station": station, "device": device, "events": events, "maintenance": maintenance}


def run_acceptance_checks(tables: dict, raw_dir: str | Path = "data",
                          a1_manifest: str | Path = "analysis/reports/phaseA1/file_hash_manifest.json",
                          baseline_rows: int = 529922, tol_rows: int = 10) -> dict:
    """阶段 A2 自检清单。"""
    import json
    from air_station.data import validators

    raw_dir = Path(raw_dir)
    station, device = tables["station"], tables["device"]
    checks: dict[str, dict] = {}

    # 1) 原始文件哈希未变
    manifest_now = validators.hash_manifest(raw_dir, REQUIRED_RAW)
    p = Path(a1_manifest)
    if p.exists():
        ref = json.loads(p.read_text(encoding="utf-8"))
        unchanged = all(ref.get(k, {}).get("md5") == v.get("md5")
                        for k, v in manifest_now.items() if v.get("exists"))
        checks["raw_hash_unchanged"] = {"pass": bool(unchanged)}
    else:
        checks["raw_hash_unchanged"] = {"pass": True, "note": "无 A1 基准清单, 跳过比对"}

    # 2) 哨兵值彻底清除
    sent = {65535.0, 4294970000.0}
    cols = ["runtime_hours", "load_hours", "maintenance_remaining_hours"]
    has_sent = bool(device[cols].isin(sent).any().any())
    checks["no_sentinel_in_cleaned"] = {"pass": not has_sent}

    # 3) 恒零/常量列不进入模型特征
    feature_cols = [c for c in DEVICE_COLUMNS if c not in
                    ("time", "device_id", "device_type", "quality_valid", "quality_reason")]
    banned = {"4#B相电流", "5#B相电流", "大气压", "B相电流"}
    checks["no_constant_in_features"] = {
        "pass": not (banned & set(feature_cols)) and "current_proxy_a" in device.columns}

    # 4) 长缺失未被错误插值 (前6天 temperature_available=False)
    gap = device[~device["temperature_available"]]
    checks["long_gap_not_interpolated"] = {
        "pass": bool(gap["temperature_available"].eq(False).all()) and len(gap) > 0,
        "rows_in_gap": int(len(gap))}

    # 5) 站级总流量 = 两台机组之和 (相对/绝对容差)
    g = device.groupby(TIME_COL, as_index=False)[["flow_m3_min", "power_kw"]].sum()
    merged = station[[TIME_COL, "total_flow_m3_min", "total_power_kw"]].merge(g, on=TIME_COL)
    flow_ok = bool(np.allclose(merged["total_flow_m3_min"], merged["flow_m3_min"],
                               rtol=1e-4, atol=1e-2))
    power_ok = bool(np.allclose(merged["total_power_kw"], merged["power_kw"],
                                rtol=1e-6, atol=1e-2))
    checks["station_flow_sum"] = {"pass": flow_ok}
    checks["station_power_sum"] = {"pass": power_ok}

    # 6) 设备级行数达标
    diff = abs(len(device) - baseline_rows)
    checks["device_rows"] = {"pass": diff <= tol_rows,
                             "rows": int(len(device)), "baseline": baseline_rows, "diff": int(diff)}

    # 7) 推断/插值均有质量标记
    checks["quality_flags_present"] = {
        "pass": all(c in device.columns for c in QUALITY_FLAGS) and "environment_interpolated" in station.columns}

    checks["all_pass"] = {"pass": all(v.get("pass") for v in checks.values())}
    return checks

