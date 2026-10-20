"""B1 基线评估: 规则基线与历史实测轨迹的指标计算 (任务书 V1.1 §5.2, §9)。

两类输出严格分离:
  - manual_rule_baseline: 传统规则基线的动作与约束结果;
  - observed_operation : 历史真实运行轨迹 (参考基准, 不是人工策略复现)。

指标口径与 B5 复用; 所有样本剔除规则来自 configs/baseline.yaml 的 evaluation.exclude。
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

from air_station.optimization import contracts as C
from air_station.optimization.contracts import DispatchResult, safe_float


def _segments(values: Iterable[int]) -> list[tuple[int, int]]:
    vals = [int(v) for v in values]
    if not vals:
        return []
    segs = []
    cur, length = vals[0], 1
    for v in vals[1:]:
        if v == cur:
            length += 1
        else:
            segs.append((cur, length))
            cur, length = v, 1
    segs.append((cur, length))
    return segs


def count_switches(values: Iterable[int], min_segment_minutes: int = 5) -> int:
    """统计运行状态翻转次数, 先合并短于阈值的毛刺段。"""
    cleaned: list[tuple[int, int]] = []
    for val, length in _segments(values):
        if length < min_segment_minutes and cleaned:
            cleaned[-1] = (cleaned[-1][0], cleaned[-1][1] + length)
        else:
            cleaned.append((val, length))
    return sum(1 for i in range(1, len(cleaned)) if cleaned[i][0] != cleaned[i - 1][0])


def observed_metrics(device_df: pd.DataFrame, station_df: pd.DataFrame,
                     config: Optional[dict] = None) -> dict:
    """历史实测运行轨迹指标 (observed_operation)。"""
    cfg = config or {}
    ex = cfg.get("evaluation", {}).get("exclude", {})
    bar = cfg.get("pressure", {}).get("band_mpa", [0.55, 0.66])
    lo, hi = float(bar[0]), float(bar[1])

    st = station_df.copy()
    if ex.get("zero_pressure", True) and "header_pressure_mpa" in st:
        st = st[st["header_pressure_mpa"] > 0]

    energy = float(st["total_power_kw"].sum() / 60.0) if "total_power_kw" in st else None
    volume = float(st["total_flow_m3_min"].sum()) if "total_flow_m3_min" in st else None
    sec = (energy / volume) if (energy and volume) else None

    if "header_pressure_mpa" in st and len(st):
        p = st["header_pressure_mpa"]
        compliance = float(((p >= lo) & (p <= hi)).mean())
        pressure_std = float(p.std(ddof=0))
    else:
        compliance, pressure_std = None, None

    device_metrics = {}
    min_seg = int(ex.get("short_segment_minutes", 5))
    for did, sub in device_df.groupby("device_id"):
        sub = sub.sort_values("time")
        running = sub["running"].astype(int).tolist()
        run_rows = sub[sub["running"].astype(bool)] if "running" in sub else sub.iloc[0:0]
        if ex.get("running_zero_flow", True) and "flow_m3_min" in run_rows:
            valid = run_rows[run_rows["flow_m3_min"] > 0]
        else:
            valid = run_rows
        overload = None
        if "load_ratio" in valid and len(valid):
            overload = float((valid["load_ratio"] > 1.0).mean())
        device_metrics[C.normalize_device_id(did)] = {
            "running_share": round(float(np.mean(running)) if running else 0.0, 4),
            "switch_count": int(count_switches(running, min_seg)),
            "overload_rate": round(overload, 4) if overload is not None else None,
            "energy_kwh": round(float(valid["power_kw"].sum() / 60.0), 1) if "power_kw" in valid and len(valid) else None,
            "volume_m3": round(float(valid["flow_m3_min"].sum()), 1) if "flow_m3_min" in valid and len(valid) else None,
        }

    return {
        "source": "observed_operation",
        "station": {
            "energy_kwh": round(energy, 1) if energy is not None else None,
            "volume_m3": round(volume, 1) if volume is not None else None,
            "sec_kwh_per_m3": round(sec, 5) if sec is not None else None,
            "pressure_compliance_rate": round(compliance, 4) if compliance is not None else None,
            "pressure_std_mpa": round(pressure_std, 4) if pressure_std is not None else None,
        },
        "devices": device_metrics,
    }


def summarize_reason_codes(results: Iterable[DispatchResult]) -> dict:
    hist: dict[str, int] = {}
    for r in results:
        for rc in r.reason_codes:
            hist[rc] = hist.get(rc, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])))


def baseline_metrics(results: list[DispatchResult], config: Optional[dict] = None) -> dict:
    """规则基线动作与约束汇总 (manual_rule_baseline)。"""
    n = len(results)
    if n == 0:
        return {"source": "manual_rule_baseline", "n_steps": 0}
    status_hist: dict[str, int] = {}
    validation_hist: dict[str, int] = {}
    starts = stops = 0
    infeasible = insufficient = 0
    for r in results:
        status_hist[r.status] = status_hist.get(r.status, 0) + 1
        validation_hist[r.validation_mode] = validation_hist.get(r.validation_mode, 0) + 1
        for a in r.device_actions:
            if a.action == "start":
                starts += 1
            elif a.action == "stop":
                stops += 1
        if r.status == C.STATUS_INFEASIBLE:
            infeasible += 1
        elif r.status in (C.STATUS_INSUFFICIENT_DATA, C.STATUS_INVALID_INPUT):
            insufficient += 1

    feasible = status_hist.get(C.STATUS_SUCCESS, 0)
    return {
        "source": "manual_rule_baseline",
        "n_steps": n,
        "feasible_rate": round(feasible / n, 4),
        "status_histogram": status_hist,
        "validation_mode_histogram": validation_hist,
        "planned_starts": starts,
        "planned_stops": stops,
        "infeasible_steps": infeasible,
        "insufficient_data_steps": insufficient,
        "reason_codes": summarize_reason_codes(results),
    }


def compare_heads_up(baseline: dict, observed: dict) -> dict:
    """给出规则基线与历史实测的并列摘要 (不做不公平比较, 仅并列展示)。"""
    return {"manual_rule_baseline": baseline, "observed_operation": observed}
