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

# A6 使用 `as_of`; 旧版使用 `time`; 两者兼容 (issue 时刻)
ISSUE_COLUMNS = ("as_of", "time")
REQUIRED_COLUMNS = ["horizon_minutes", "predicted_flow"]


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

    # ---- 多步规划器对齐: 15 分钟步长, n 步 ----
    def demand_series_for_steps(self, as_of, step_minutes: int, n_steps: int,
                                missing_policy: str = "hold_previous") -> list[float]:
        """把 A 的 15/30/60 min 预测对齐到 step_minutes x n_steps 规划器。

        - 第 k 步 (0-based) 的目标时刻 = as_of + (k+1)*step_minutes, 需要的 horizon 即该值;
        - 若该 horizon 在**同一发出时刻 as_of** 的预测中不存在 (如 step=15,n=4 需要 45min,
          A 只给 15/30/60), 按 missing_policy 处理, **只使用 as_of 当时已可得的信息**:
            * "hold_previous": 取 <= 目标 horizon 的最大可用 horizon 的预测 (无未来泄漏);
            * "strict": 直接 KeyError。
        - **绝不**使用 `actual_flow` 或未来时刻的数据补齐。
        """
        ts = pd.Timestamp(as_of)
        rows = self.by_issue.get(ts, {})
        if not rows:
            raise KeyError(f"as_of={ts} 无任何预测")
        avail = sorted(h for h, r in rows.items() if r.get("predicted_flow") is not None)
        out: list[float] = []
        for k in range(int(n_steps)):
            target_h = (k + 1) * int(step_minutes)
            if target_h in rows and rows[target_h].get("predicted_flow") is not None:
                out.append(float(rows[target_h]["predicted_flow"]))
                continue
            if missing_policy == "strict":
                raise KeyError(f"缺预测: as_of={ts} horizon={target_h}")
            prev = [h for h in avail if h <= target_h]
            if not prev:
                raise KeyError(f"as_of={ts} 无 <= {target_h}min 的可用预测(不可用未来信息补齐)")
            out.append(float(rows[prev[-1]]["predicted_flow"]))
        return out

    # ---- 评估专用 (actual) ----
    def actual_at_target(self, as_of, horizon_minutes: int) -> Optional[float]:
        tgt = pd.Timestamp(as_of) + pd.Timedelta(minutes=int(horizon_minutes))
        return self.actual_by_target.get(tgt)


def load_forecast_bundle(path: str | Path, meta: Optional[ForecastMeta] = None,
                         scoring: Optional[pd.DataFrame] = None,
                         scoring_split: Optional[str] = None,
                         strict_target_time: bool = True) -> ForecastBundle:
    """加载 A 交付预测表 (兼容 A6 `as_of` 与旧版 `time`)。

    - `scoring`: 独立的评分真值表 (A6 为 forecast_scoring_actuals.parquet); 若提供, 仅
      用其 `actual_flow`(+`actual_available`) 填充 `actual_by_target`, **不进入调度输入**;
    - `strict_target_time`: 若表中含 `target_time`, 校验 == as_of + horizon (不符报错)。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[forecast_adapter] 预测文件缺失: {p}")
    try:
        df = pd.read_parquet(p)
    except EmptyDataError as e:
        raise ValueError(f"[forecast_adapter] 预测文件为空: {p}") from e
    issue_col = next((c for c in ISSUE_COLUMNS if c in df.columns), None)
    missing = ([c for c in REQUIRED_COLUMNS if c not in df.columns]
               + ([] if issue_col else ["as_of|time"]))
    if missing:
        raise ValueError(f"[forecast_adapter] 缺列: {missing}; 需要 issue 列之一 {ISSUE_COLUMNS}")
    if df.empty:
        raise ValueError("[forecast_adapter] 预测表为空")
    df = df.copy()
    df["issue_ts"] = pd.to_datetime(df[issue_col])
    df["horizon_minutes"] = df["horizon_minutes"].astype(int)
    if strict_target_time and "target_time" in df.columns:
        expect = df["issue_ts"] + pd.to_timedelta(df["horizon_minutes"], unit="m")
        if not bool((pd.to_datetime(df["target_time"]) == expect).all()):
            raise ValueError("[forecast_adapter] target_time != as_of + horizon_minutes")
    meta = meta or ForecastMeta()
    if meta.sample_minutes is None:
        diffs = df["issue_ts"].drop_duplicates().sort_values().diff().dropna()
        if len(diffs):
            meta.sample_minutes = int(diffs.dt.total_seconds().mode().iloc[0] // 60)
    bundle = ForecastBundle(horizons=sorted(df["horizon_minutes"].unique().tolist()), meta=meta)
    for row in df.itertuples(index=False):
        h = int(row.horizon_minutes)
        rec = {"predicted_flow": _f(row.predicted_flow),
               "lower_bound": _f(getattr(row, "lower_bound", None)),
               "upper_bound": _f(getattr(row, "upper_bound", None))}
        bundle.by_issue.setdefault(pd.Timestamp(row.issue_ts), {})[h] = rec
        # 内嵌 actual_flow (旧格式) —— 仅评估
        act = _f(getattr(row, "actual_flow", None))
        if act is not None:
            bundle.actual_by_target[pd.Timestamp(row.issue_ts) + pd.Timedelta(minutes=h)] = act
    if scoring is not None:
        attach_scoring_actuals(bundle, scoring, split=scoring_split)
    if meta.data_cutoff is None:
        lastissue_ts = max(bundle.by_issue)
        meta.data_cutoff = (lastissue_ts + pd.Timedelta(minutes=max(bundle.horizons))).isoformat()
    return bundle


def attach_scoring_actuals(bundle: ForecastBundle, scoring: pd.DataFrame,
                           split: Optional[str] = None) -> int:
    """从独立评分表填充 actual_by_target (仅评估); 返回写入条数。"""
    s = scoring.copy()
    if split is not None and "split" in s.columns:
        s = s[s["split"].astype(str) == str(split)]
    s = s[s["horizon_minutes"].notna()]
    n = 0
    for row in s.itertuples(index=False):
        if hasattr(row, "actual_available") and not bool(row.actual_available):
            continue
        act = _f(getattr(row, "actual_flow", None))
        if act is None:
            continue
        h = int(row.horizon_minutes)
        tgt = pd.Timestamp(getattr(row, "target_time")) if "target_time" in s.columns \
            else pd.Timestamp(getattr(row, "as_of", getattr(row, "time"))) + pd.Timedelta(minutes=h)
        bundle.actual_by_target[tgt] = act
        n += 1
    return n


def _f(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    return float(v)
