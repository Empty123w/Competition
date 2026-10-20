"""B1 历史回放流水线: 按 as_of 滚动调用规则引擎, 保证确定性与时间边界。

边界规则 (任务书 V1.1 §4.5):
  - 每个决策时刻只使用该时刻之前可获得的数据 (snapshot / forecast / health);
  - 历史实测轨迹单独由 evaluator.observed_metrics 计算, 不混入规则基线。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from air_station.optimization.contracts import (
    DeviceSnapshot,
    DispatchConstraints,
    DispatchResult,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    StationSnapshot,
    normalize_device_id,
)
from air_station.baseline import rules

DEFAULT_PROCESSED_DIR = "data/processed"
DEFAULT_FORECAST_PATH = "artifacts/metrics/forecast_test_predictions.parquet"


def load_processed(processed_dir: str | Path = DEFAULT_PROCESSED_DIR):
    base = Path(processed_dir)
    dev_path = base / "device_timeseries.parquet"
    st_path = base / "station_timeseries.parquet"
    if not dev_path.exists():
        raise FileNotFoundError(f"[baseline] 设备数据缺失: {dev_path}")
    if not st_path.exists():
        raise FileNotFoundError(f"[baseline] 站级数据缺失: {st_path}")
    dev = pd.read_parquet(dev_path, columns=[
        "time", "device_id", "running", "flow_m3_min", "power_kw", "load_ratio",
        "discharge_pressure_mpa", "igv_percent", "bov_closed_percent", "quality_valid"])
    st = pd.read_parquet(st_path, columns=[
        "time", "header_pressure_mpa", "total_flow_m3_min", "total_power_kw"])
    dev["time"] = pd.to_datetime(dev["time"])
    st["time"] = pd.to_datetime(st["time"])
    dev["device_id"] = dev["device_id"].map(normalize_device_id)
    return dev.sort_values(["device_id", "time"]).reset_index(drop=True), \
        st.sort_values("time").reset_index(drop=True)


def load_frozen_forecasts(forecast_path: str | Path = DEFAULT_FORECAST_PATH,
                          horizon_minutes: int = 60) -> dict[pd.Timestamp, ForecastResult]:
    """从 A 冻结测试集预测表构建 as_of -> ForecastResult 映射。"""
    p = Path(forecast_path)
    if not p.exists():
        raise FileNotFoundError(f"[baseline] 预测文件缺失: {p}")
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"])
    df = df[df["horizon_minutes"] == horizon_minutes]
    out: dict[pd.Timestamp, ForecastResult] = {}
    for row in df.itertuples(index=False):
        out[row.time] = ForecastResult(
            as_of=pd.Timestamp(row.time).isoformat(),
            horizon_minutes=int(row.horizon_minutes),
            predicted_flow=float(row.predicted_flow),
            lower_bound=float(row.lower_bound),
            upper_bound=float(row.upper_bound),
            forecast_id=f"frozen-{int(row.horizon_minutes)}m-{pd.Timestamp(row.time).isoformat()}",
        )
    return out


def snapshot_from_frames(device_df: pd.DataFrame, station_df: pd.DataFrame,
                         as_of) -> StationSnapshot:
    """构建 as_of 时刻快照, 只使用 as_of 之前的数据。"""
    t = pd.Timestamp(as_of)
    st_hist = station_df[station_df["time"] <= t]
    if st_hist.empty:
        raise ValueError(f"[baseline] as_of={t} 之前无站级数据")
    st_row = st_hist.iloc[-1]
    devices = []
    for did in ("4#", "5#"):
        sub = device_df[(device_df["device_id"] == did) & (device_df["time"] <= t)]
        if sub.empty:
            continue
        r = sub.iloc[-1]
        devices.append(DeviceSnapshot(
            device_id=did,
            running=bool(r["running"]),
            flow_m3_min=_f(r["flow_m3_min"]),
            power_kw=_f(r["power_kw"]),
            discharge_pressure_mpa=_f(r["discharge_pressure_mpa"]),
            igv_percent=_f(r["igv_percent"]),
            bov_closed_percent=_f(r["bov_closed_percent"]),
            quality_valid=bool(r["quality_valid"]),
            data_source="processed_parquet",
        ))
    return StationSnapshot(as_of=t.isoformat(),
                           header_pressure_mpa=_f(st_row["header_pressure_mpa"]),
                           devices=devices, data_source="processed_parquet")


def _f(v):
    try:
        return None if pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def _sample_times(station_df: pd.DataFrame, start, end, step_minutes: int) -> list[pd.Timestamp]:
    times = station_df["time"]
    if start is not None:
        times = times[times >= pd.Timestamp(start)]
    if end is not None:
        times = times[times <= pd.Timestamp(end)]
    if times.empty:
        return []
    ordered = list(times)
    return ordered[::max(1, int(step_minutes))]


def run_baseline_from_frames(
    device_df: pd.DataFrame,
    station_df: pd.DataFrame,
    forecasts: Optional[dict] = None,
    config: Optional[dict] = None,
    *,
    start=None,
    end=None,
    step_minutes: int = 5,
    health_provider: Optional[Callable[[pd.Timestamp], list[HealthAssessment]]] = None,
) -> list[DispatchResult]:
    """在给定帧上执行规则基线回放, 返回逐步 DispatchResult 列表 (确定性)。"""
    cfg = config or {}
    constraints = DispatchConstraints.from_config(cfg)
    confirm = constraints.confirm_minutes
    forecasts = forecasts or {}
    health_provider = health_provider or (lambda _t: [])

    st = station_df.sort_values("time").reset_index(drop=True)
    pressures = list(zip(st["time"], st["header_pressure_mpa"]))
    sample_times = _sample_times(st, start, end, step_minutes)

    state = PolicyState()
    ptr = 0
    results: list[DispatchResult] = []
    for t in sample_times:
        while ptr < len(pressures) and pressures[ptr][0] <= t:
            ptr += 1
        window = pressures[max(0, ptr - confirm):ptr]
        state.pressure_history = [(str(tt), float(v)) for tt, v in window if pd.notna(v)]
        snap = snapshot_from_frames(device_df, st, t)
        fc = forecasts.get(pd.Timestamp(t))
        res = rules.decide(snap, fc, health_provider(pd.Timestamp(t)), constraints, cfg, state)
        for a in res.device_actions:
            if a.action in ("start", "stop"):
                state.last_switch_at[a.device_id] = str(t)
                state.recent_switch_times.append(str(t))
        results.append(res)
    return results


def run_baseline(
    processed_dir: str | Path = DEFAULT_PROCESSED_DIR,
    config_path: str | Path = rules.DEFAULT_CONFIG,
    forecast_path: str | Path = DEFAULT_FORECAST_PATH,
    *,
    start=None,
    end=None,
    step_minutes: int = 15,
    use_frozen_forecasts: bool = True,
) -> list[DispatchResult]:
    """便捷入口: 读取标准 Parquet 与冻结预测, 在窗口内回放规则基线。"""
    cfg = rules.load_config(config_path)
    device_df, station_df = load_processed(processed_dir)
    forecasts = {}
    if use_frozen_forecasts and Path(forecast_path).exists():
        forecasts = load_frozen_forecasts(forecast_path,
                                          cfg.get("demand", {}).get("horizon_minutes", 60))
    return run_baseline_from_frames(device_df, station_df, forecasts, cfg,
                                    start=start, end=end, step_minutes=step_minutes)


if __name__ == "__main__":
    import json
    from air_station.baseline import evaluator
    res = run_baseline(step_minutes=60)
    summary = evaluator.baseline_metrics(res)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
