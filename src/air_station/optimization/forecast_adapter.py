"""A 历史预测接入适配器 (新增模块, 不改冻结 B1-B4/B5)。

把 A 交付的预测表转换为:
  - `ForecastResult` (as_of = 发出时刻);
  - 决策时刻 t 的多时域需求序列 (target = t + horizon), 仅用于前瞻规划;
  - 独立的 `actual` 真值序列 (仅供评估, **禁止**作为未来调度输入)。

字段/口径约定 (须与 A 交付一致, 由 meta 记录):
  time            预测**发出**时刻 (本地时间 Asia/Shanghai; 不与 UTC 静默混用)
  horizon_minutes 预测时域 (15/30/60)
  predicted_flow  目标时刻 = time + horizon_minutes 的预测流量 (m3/min)
  lower_bound/upper_bound  区间下/上界 (分位数含义由 meta.quantile 指定, 默认 0.05/0.95)
  actual_flow     目标时刻真值 (m3/min) —— **评估专用, 不得作为调度输入**

重要:
  - 预测是**目标时刻的量**, 不能把瞬时预测直接当"区间平均流量"去积分;
  - `demand_series(as_of)` 取各时域在 as_of 的预测, 对齐 target 时刻;
  - 时间步与采样频率由 meta 校验; 缺时域/缺行 -> 结构化错误, 不默认正常。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
from pandas.errors import EmptyDataError

from air_station.optimization.contracts import ForecastResult

REQUIRED_COLUMNS = ["time", "horizon_minutes", "predicted_flow"]


@dataclass
class ForecastMeta:
    quantile_lower: float = 0.05
    quantile_upper: float = 0.95
    timezone: str = "Asia/Shanghai"
    unit_flow: str = "m3/min"
    sample_minutes: Optional[int] = None
    data_cutoff: Optional[str] = None
    source: str = "A_historical_forecast"
    note: str = ""


@dataclass
class ForecastBundle:
    by_issue: dict[pd.Timestamp, dict[int, dict]] = field(default_factory=dict)
    actual_by_target: dict[pd.Timestamp, float] = field(default_factory=dict)
    horizons: list[int] = field(default_factory=list)
    meta: ForecastMeta = field(default_factory=ForecastMeta)

    # ---- 调度输入 (predictions) ----
    def forecast_result(self, as_of, horizon_minutes: int) -> Optional[ForecastResult]:
        row = self.by_issue.get(pd.Timestamp(as_of), {}).get(int(horizon_minutes))
        if row is None:
            return None
        return ForecastResult(
            as_of=pd.Timestamp(as_of).isoformat(), horizon_minutes=int(horizon_minutes),
            predicted_flow=float(row["predicted_flow"]),
            lower_bound=(float(row["lower_bound"]) if row.get("lower_bound") is not None else None),
            upper_bound=(float(row["upper_bound"]) if row.get("upper_bound") is not None else None),
            forecast_id=f"A-{int(horizon_minutes)}m-{pd.Timestamp(as_of).isoformat()}")

    def demand_series(self, as_of, hours_minutes: list[int]) -> dict[int, float]:
        """返回 {horizon: 目标时刻需求}; 任一缺失抛 KeyError (不默认正常)。"""
        ts = pd.Timestamp(as_of)
        out: dict[int, float] = {}
        for h in hours_minutes:
            row = self.by_issue.get(ts, {}).get(int(h))
            if row is None or row.get("predicted_flow") is None:
                raise KeyError(f"缺预测: as_of={ts} horizon={h}")
            out[int(h)] = float(row["predicted_flow"])
        return out

    # ---- 评估专用 (actual) ----
    def actual_at_target(self, as_of, horizon_minutes: int) -> Optional[float]:
        tgt = pd.Timestamp(as_of) + pd.Timedelta(minutes=int(horizon_minutes))
        return self.actual_by_target.get(tgt)


def load_forecast_bundle(path: str | Path, meta: Optional[ForecastMeta] = None) -> ForecastBundle:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[forecast_adapter] 预测文件缺失: {p}")
    try:
        df = pd.read_parquet(p)
    except EmptyDataError as e:
        raise ValueError(f"[forecast_adapter] 预测文件为空: {p}") from e
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"[forecast_adapter] 缺列: {missing}; 需要 {REQUIRED_COLUMNS}")
    if df.empty:
        raise ValueError("[forecast_adapter] 预测表为空")
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    df["horizon_minutes"] = df["horizon_minutes"].astype(int)
    meta = meta or ForecastMeta()
    if meta.sample_minutes is None:
        diffs = df["time"].drop_duplicates().sort_values().diff().dropna()
        if len(diffs):
            meta.sample_minutes = int(diffs.dt.total_seconds().mode().iloc[0] // 60)
    bundle = ForecastBundle(horizons=sorted(df["horizon_minutes"].unique().tolist()), meta=meta)
    for row in df.itertuples(index=False):
        h = int(row.horizon_minutes)
        rec = {"predicted_flow": _f(row.predicted_flow),
               "lower_bound": _f(getattr(row, "lower_bound", None)),
               "upper_bound": _f(getattr(row, "upper_bound", None))}
        bundle.by_issue.setdefault(pd.Timestamp(row.time), {})[h] = rec
        act = _f(getattr(row, "actual_flow", None))
        if act is not None:
            bundle.actual_by_target[pd.Timestamp(row.time) + pd.Timedelta(minutes=h)] = act
    if meta.data_cutoff is None:
        last_issue = max(bundle.by_issue)
        meta.data_cutoff = (last_issue + pd.Timedelta(minutes=max(bundle.horizons))).isoformat()
    return bundle


def _f(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return float(v)
