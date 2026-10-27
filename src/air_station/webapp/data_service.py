"""可视化页面的数据服务层。

集中封装：读取离线产物（metrics / parquet / B 侧 JSON），
以及调用各模块已实现的运行时接口（预测 / 健康 / RUL / 调度 / 闭环仿真）。

设计原则：
  - 只调用已实现的公开入口，不改动任何算法与协议；
  - 结果做缓存（lru_cache）避免重复加载大 parquet；
  - 对可选产物做防御式读取，缺失时返回明确的 unavailable 标记，不伪造数据。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PROCESSED = ROOT / "data" / "processed"
METRICS = ROOT / "artifacts" / "metrics"
MODELS = ROOT / "artifacts" / "models"
CONFIGS = ROOT / "configs"
HANDOFF_B = ROOT / "artifacts" / "handoff_b"
REPORTS_B = ROOT / "analysis" / "reports" / "b"
SIM_DIR = REPORTS_B / "simulation"
DELIVERY = REPORTS_B / "delivery"

MAX_POINTS = 900


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa
        return {"_error": f"{type(exc).__name__}: {exc}"}
    return default


def _num(x) -> float | None:
    try:
        v = float(x)
        if math.isfinite(v):
            return round(v, 4)
    except (TypeError, ValueError):
        pass
    return None


def _clean(vals) -> list:
    return [_num(v) for v in vals]


def _downsample(df: pd.DataFrame, max_points: int = MAX_POINTS,
                time_col: str = "time") -> pd.DataFrame:
    if len(df) <= max_points:
        return df
    step = math.ceil(len(df) / max_points)
    return df.iloc[::step]


def _fmt_time(series: pd.Series) -> list[str]:
    return [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") for t in series]


# --------------------------------------------------------------------------- #
# 指标与清单
# --------------------------------------------------------------------------- #
_METRIC_FILES = {
    "forecast": "forecast_metrics.json",
    "forecast_rolling": "forecast_rolling_metrics.json",
    "health": "health_metrics.json",
    "rul": "rul_metrics.json",
    "pressure": "pressure_metrics.json",
    "performance": "performance_metrics.json",
}


@lru_cache(maxsize=1)
def load_metrics() -> dict:
    return {k: _read_json(METRICS / v) for k, v in _METRIC_FILES.items()}


@lru_cache(maxsize=1)
def data_manifest() -> dict:
    return {
        "data": _read_json(PROCESSED / "data_manifest.json", {}),
        "feature": _read_json(PROCESSED / "feature_manifest.json", {}),
    }


@lru_cache(maxsize=1)
def forecast_meta() -> dict:
    return _read_json(MODELS / "forecast_meta.json", {})


@lru_cache(maxsize=1)
def rul_meta() -> dict:
    return _read_json(MODELS / "rul_meta.json", {})


# --------------------------------------------------------------------------- #
# 标准时序数据（站级 / 设备级）
# --------------------------------------------------------------------------- #
def _resample(df: pd.DataFrame, rule: str, value_cols: list[str],
              time_col: str = "time", how: str = "mean") -> pd.DataFrame:
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    out = df.set_index(time_col).sort_index()
    keep = [c for c in value_cols if c in out.columns]
    agg = getattr(out[keep].resample(rule), how)()
    return agg.reset_index()


@lru_cache(maxsize=1)
def station_series() -> dict:
    df = pd.read_parquet(PROCESSED / "station_timeseries.parquet")
    cols = ["header_pressure_mpa", "total_flow_m3_min", "total_power_kw",
            "station_temperature_c", "station_humidity_percent", "load_rate"]
    hourly = _resample(df, "1h", cols)
    hourly = _downsample(hourly)
    return {
        "time": _fmt_time(hourly["time"]),
        "header_pressure_mpa": _clean(hourly["header_pressure_mpa"]),
        "total_flow_m3_min": _clean(hourly["total_flow_m3_min"]),
        "total_power_kw": _clean(hourly["total_power_kw"]),
        "station_temperature_c": _clean(hourly.get("station_temperature_c", pd.Series(dtype=float))),
        "station_humidity_percent": _clean(hourly.get("station_humidity_percent", pd.Series(dtype=float))),
        "load_rate": _clean(hourly.get("load_rate", pd.Series(dtype=float))),
    }


_DEVICE_COLS = ["flow_m3_min", "power_kw", "load_ratio", "discharge_pressure_mpa",
                "igv_percent", "vibration_stage_1_mm_s", "vibration_stage_2_mm_s",
                "vibration_stage_3_mm_s", "discharge_temperature_c", "fault_code"]


@lru_cache(maxsize=4)
def device_series(device: str) -> dict:
    dev = pd.read_parquet(PROCESSED / "device_timeseries.parquet",
                          columns=["time", "device_id"] + _DEVICE_COLS)
    dev["time"] = pd.to_datetime(dev["time"])
    key = str(device).replace("#", "")
    sub = dev[dev["device_id"].astype(str).str.replace("#", "") == key]
    hourly = _resample(sub, "1h", _DEVICE_COLS)
    hourly = _downsample(hourly)
    out = {"time": _fmt_time(hourly["time"])}
    for c in _DEVICE_COLS:
        out[c] = _clean(hourly[c]) if c in hourly.columns else []
    return out


# --------------------------------------------------------------------------- #
# 历史预测 / 健康 / RUL 序列
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def forecast_series(horizon: int) -> dict:
    df = pd.read_parquet(METRICS / "forecast_test_predictions.parquet")
    df["time"] = pd.to_datetime(df["time"])
    sub = df[df["horizon_minutes"] == int(horizon)].sort_values("time")
    sub = _downsample(sub)
    return {
        "horizon_minutes": int(horizon),
        "time": _fmt_time(sub["time"]),
        "predicted_flow": _clean(sub["predicted_flow"]),
        "lower_bound": _clean(sub["lower_bound"]),
        "upper_bound": _clean(sub["upper_bound"]),
        "actual_flow": _clean(sub["actual_flow"]),
    }


@lru_cache(maxsize=4)
def health_history_series(device: str) -> dict:
    path = METRICS / "health_history.parquet"
    if not path.exists():
        return {"available": False}
    df = pd.read_parquet(path)
    df["time"] = pd.to_datetime(df["time"])
    sub = df[df["device_id"].astype(str).str.replace("#", "") == str(device).replace("#", "")]
    sub = sub.sort_values("time")
    sub = _downsample(sub)
    return {
        "available": True,
        "time": _fmt_time(sub["time"]),
        "health_score": _clean(sub["health_score"]),
        "anomaly_score": _clean(sub["anomaly_score"]),
        "risk_level": [None if pd.isna(v) else str(v) for v in sub["risk_level"]],
    }


@lru_cache(maxsize=4)
def rul_history_series(device: str) -> dict:
    path = METRICS / "rul_history.parquet"
    if not path.exists():
        return {"available": False}
    df = pd.read_parquet(path)
    df["time"] = pd.to_datetime(df["time"])
    sub = df[df["device_id"].astype(str) == str(device).replace("#", "")]
    sub = sub.sort_values("time")
    sub = _downsample(sub)
    return {
        "available": True,
        "time": _fmt_time(sub["time"]),
        "estimated_rul_hours": _clean(sub["estimated_rul_hours"]),
        "lower_bound_hours": _clean(sub["lower_bound_hours"]),
        "upper_bound_hours": _clean(sub["upper_bound_hours"]),
        "quality_valid": [bool(v) for v in sub["quality_valid"]],
    }


# --------------------------------------------------------------------------- #
# 运行时接口（预测 / 健康 / RUL）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def run_forecast() -> dict:
    try:
        from air_station.models.forecasting.service import forecast_json
        return forecast_json(processed_dir=str(PROCESSED),
                             config_path=str(CONFIGS / "forecasting.yaml"),
                             model_dir=str(MODELS))
    except Exception as exc:  # noqa
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


@lru_cache(maxsize=1)
def run_health() -> dict:
    try:
        from air_station.models.health.service import health_json
        return health_json(processed_dir=str(PROCESSED),
                           thresholds_path=str(CONFIGS / "health_thresholds.yaml"))
    except Exception as exc:  # noqa
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


@lru_cache(maxsize=1)
def run_rul() -> dict:
    try:
        from air_station.models.rul.service import rul_json
        return rul_json(config_path=str(CONFIGS / "rul.yaml"),
                        sequences_path=str(ROOT / "data" / "simulated" / "rul_sequences.parquet"),
                        model_dir=str(MODELS))
    except Exception as exc:  # noqa
        return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# 调度（B 侧统一接口）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _load_processed():
    from air_station.baseline import pipeline as PIPE
    return PIPE.load_processed(str(PROCESSED))


@lru_cache(maxsize=1)
def _load_bundle():
    from air_station.optimization.forecast_adapter import load_forecast_bundle
    preds = HANDOFF_B / "forecast_validation_dispatch_calibrated.parquet"
    scoring = HANDOFF_B / "forecast_scoring_actuals_calibrated.parquet"
    if not preds.exists():
        preds = HANDOFF_B / "forecast_validation_dispatch.parquet"
        scoring = HANDOFF_B / "forecast_scoring_actuals.parquet"
    scoring_df = pd.read_parquet(scoring) if scoring.exists() else None
    return load_forecast_bundle(preds, scoring=scoring_df, scoring_split="valid")


@lru_cache(maxsize=1)
def dispatch_times() -> list[str]:
    try:
        bundle = _load_bundle()
    except Exception:
        return []
    times = sorted(t for t, rows in bundle.by_issue.items()
                   if all(h in rows for h in (15, 30, 45, 60)))
    if not times:
        return []
    step = max(1, len(times) // 240)
    return [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") for t in times[::step]]


def _health_at(as_of) -> list:
    from air_station.optimization import contracts as C
    out = []
    df = _health_history_df()
    if df is None:
        return out
    t = pd.Timestamp(as_of)
    for dev in (4, 5):
        sub = df[(df["device_id"].astype(str) == str(dev)) & (df["time"] <= t)]
        if sub.empty:
            continue
        r = sub.iloc[-1]
        out.append(C.HealthAssessment(
            device_id=f"{dev}#",
            risk_level=str(r["risk_level"]) if pd.notna(r["risk_level"]) else "关注",
            health_score=float(r["health_score"]) if pd.notna(r["health_score"]) else None,
            suggestion="", source="unsupervised_history"))
    return out


@lru_cache(maxsize=1)
def _health_history_df():
    path = METRICS / "health_history.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["time", "device_id", "health_score", "risk_level"])
    df["time"] = pd.to_datetime(df["time"])
    return df


def _snapshot_json(snap) -> dict:
    d = snap.to_dict()
    return d


@lru_cache(maxsize=256)
def run_dispatch(mode: str, as_of: str | None = None) -> dict:
    from air_station.baseline import rules as B1
    from air_station.optimization import contracts as C
    from air_station.optimization import dispatch_api as API
    from air_station.baseline import pipeline as PIPE

    dev, st = _load_processed()
    bundle = _load_bundle()
    if as_of is None:
        times = sorted(t for t, rows in bundle.by_issue.items()
                       if all(h in rows for h in (15, 30, 45, 60)))
        if not times:
            raise ValueError("无可用预测时刻")
        t = times[-1]
    else:
        t = pd.Timestamp(as_of)

    snap = PIPE.snapshot_from_frames(dev, st, t)
    con = C.DispatchConstraints.from_config(B1.load_config(str(CONFIGS / "baseline.yaml")))
    fr = bundle.forecast_result(t, 15)
    if fr is None:
        return {"status": "error", "message": f"时刻 {t} 无 15 分钟预测"}
    health = _health_at(t)
    out = API.plan_dispatch(mode, snap, fr, health, con,
                            config_path=str(CONFIGS / "optimization.yaml"),
                            baseline_config_path=str(CONFIGS / "baseline.yaml"))
    return {
        "as_of": pd.Timestamp(t).strftime("%Y-%m-%d %H:%M"),
        "mode": mode,
        "snapshot": _snapshot_json(snap),
        "forecast": fr.to_dict(),
        "health": [h.to_dict() for h in health],
        "result": out,
    }


# --------------------------------------------------------------------------- #
# 闭环仿真（B 侧）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def sim_summary() -> dict:
    return _read_json(SIM_DIR / "sim_closed_loop.json", {})


@lru_cache(maxsize=8)
def sim_series(scenario: str, strategy: str) -> dict:
    path = SIM_DIR / "sim_ledger.csv"
    if not path.exists():
        return {"available": False}
    df = pd.read_csv(path)
    sub = df[(df["scenario"] == scenario) & (df["strategy"] == strategy)].sort_values("k")
    if sub.empty:
        return {"available": False}
    return {
        "available": True,
        "time": [str(t) for t in sub["t"]],
        "demand": _clean(sub["demand"]),
        "supply": _clean(sub["supply"]),
        "pressure": _clean(sub["pressure"]),
        "power_kw": _clean(sub["power_kw"]),
        "f4": _clean(sub["f4"]),
        "f5": _clean(sub["f5"]),
    }


def run_simulation_live(strategy: str, scenario: str) -> dict:
    from air_station.simulation.closed_loop import SCENARIOS, simulate
    if scenario not in SCENARIOS:
        raise ValueError(f"未知场景: {scenario}")
    r = simulate(strategy, SCENARIOS[scenario])
    recs = r["records"]
    series = {
        "time": [x["t"] for x in recs],
        "demand": _clean([x["demand"] for x in recs]),
        "supply": _clean([x["supply"] for x in recs]),
        "pressure": _clean([x["pressure"] for x in recs]),
        "power_kw": _clean([x["power_kw"] for x in recs]),
        "f4": _clean([x["f4"] for x in recs]),
        "f5": _clean([x["f5"] for x in recs]),
    }
    return {"strategy": strategy, "scenario": scenario, "metrics": r["metrics"], "series": series}


# --------------------------------------------------------------------------- #
# 其它 B 侧交付产物
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_ratio() -> dict:
    return _read_json(DELIVERY / "load_ratio_all.json", {})


@lru_cache(maxsize=1)
def call_examples() -> dict:
    return _read_json(DELIVERY / "c_call_examples.json", {})


@lru_cache(maxsize=1)
def b_unified_summary() -> dict:
    return _read_json(REPORTS_B / "b5_unified_summary.json", {})


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def overview() -> dict:
    m = load_metrics()
    fm = m.get("forecast", {}) or {}
    hm = m.get("health", {}) or {}
    rm = m.get("rul", {}) or {}
    fmetrics = fm.get("metrics", {}) if isinstance(fm, dict) else {}

    def _mape(h: str):
        try:
            return fmetrics[h]["MAPE"]
        except Exception:
            return None

    st = station_series()
    devs = {}
    try:
        for d in ("4#", "5#"):
            devs[d] = {
                "flow": device_series(d)["flow_m3_min"],
                "power": device_series(d)["power_kw"],
                "load": device_series(d)["load_ratio"],
                "vib": device_series(d)["vibration_stage_1_mm_s"],
            }
    except Exception:
        devs = {}

    # 闭环仿真：normal/b3 vs b1 的节能与压力合格率
    sim = sim_summary()
    closed = {}
    try:
        norm = sim["scenarios"]["normal"]
        b1 = norm.get("b1", {})
        b3 = norm.get("b3", {})
        closed = {
            "sec_reduction_pct": b3.get("sec_reduction_vs_b1_pct"),
            "pressure_compliance_b1": b1.get("pressure_compliance"),
            "pressure_compliance_b3": b3.get("pressure_compliance"),
            "pressure_std_reduction_pct": b3.get("pressure_std_reduction_vs_b1_pct"),
        }
    except Exception:
        closed = {}

    return {
        "range": {"start": st["time"][0] if st["time"] else None,
                  "end": st["time"][-1] if st["time"] else None,
                  "points": len(st["time"])},
        "forecast_metrics": fm,
        "health_metrics": hm,
        "rul_metrics": rm,
        "pressure_metrics": m.get("pressure", {}),
        "performance_metrics": m.get("performance", {}),
        "kpi": {
            "forecast_mape_15": _mape("15min"),
            "forecast_mape_30": _mape("30min"),
            "forecast_mape_60": _mape("60min"),
            "forecast_target_mape": fm.get("target_mape_pct"),
            "health_scores": {d: (hm.get("devices", {}).get(d, {}) or {}).get("health_score_latest")
                              for d in ("4#", "5#")},
            "health_risk": {d: (hm.get("devices", {}).get(d, {}) or {}).get("risk_level_latest")
                            for d in ("4#", "5#")},
            "rul_metrics": rm.get("metrics"),
            "closed_loop": closed,
        },
    }


# --------------------------------------------------------------------------- #
# 机组实时状态 / 模拟播放
# --------------------------------------------------------------------------- #
def rt_state(as_of: str | None = None, history_minutes: int = 90, with_reco: bool = True) -> dict:
    from air_station.baseline import pipeline as PIPE
    dev, st = _load_processed()
    if as_of is None:
        t = pd.Timestamp(st["time"].iloc[-1])
    else:
        t = pd.Timestamp(as_of)
    snap = PIPE.snapshot_from_frames(dev, st, t)
    t0 = t - pd.Timedelta(minutes=history_minutes)

    hist = st[(st["time"] > t0) & (st["time"] <= t)]
    labels = _fmt_time(hist["time"])
    dhist = {}
    for d in ("4#", "5#"): 
        sub = dev[(dev["device_id"].astype(str).str.replace("#", "") == d.replace("#", "")) &
                  (dev["time"] > t0) & (dev["time"] <= t)]
        dhist[d] = {"flow": _clean(sub["flow_m3_min"]), "power": _clean(sub["power_kw"]),
                    "load": _clean(sub["load_ratio"])}

    devices = {d.device_id: d.to_dict() for d in snap.devices}
    # 推荐模式：对三种模式求解, 取功率最低且非回退者
    rec = {"mode": None, "power_kw": None}
    if with_reco:
        for m in ("b3", "balanced", "load_balanced"):
            try:
                r = run_dispatch(m, pd.Timestamp(t).strftime("%Y-%m-%d %H:%M"))
                res = r.get("result", {})
                if res.get("status") == "success" and not res.get("is_fallback"):
                    p = res.get("estimated_power_kw")
                    if p is not None and (rec["power_kw"] is None or p < rec["power_kw"]):
                        rec = {"mode": m, "power_kw": p, "combination": res.get("target_combination")}
            except Exception:
                pass
    return {
        "as_of": pd.Timestamp(t).strftime("%Y-%m-%d %H:%M"),
        "snapshot": snap.to_dict(),
        "devices": devices,
        "station": {
            "header_pressure_mpa": snap.header_pressure_mpa,
            "total_flow_m3_min": _num(st[st["time"] <= t]["total_flow_m3_min"].iloc[-1]) if not st[st["time"] <= t].empty else None,
            "total_power_kw": _num(st[st["time"] <= t]["total_power_kw"].iloc[-1]) if not st[st["time"] <= t].empty else None,
        },
        "history": {"labels": labels, "station_pressure": _clean(hist["header_pressure_mpa"]),
                    "station_flow": _clean(hist["total_flow_m3_min"]),
                    "devices": dhist},
        "recommendation": rec,
    }


@lru_cache(maxsize=1)
def playback_times() -> list[str]:
    return dispatch_times()


# --------------------------------------------------------------------------- #
# 能效与成本分析
# --------------------------------------------------------------------------- #
PRICE_CNY_PER_KWH = 0.65  # 工程假设：工业电价, 非官方口径
_STRAT_CN = {"b1": "人工规则基线", "b1_rule": "人工规则基线", "b3_b4": "能效优先 (B3)",
             "b3": "能效优先 (B3)", "bal": "低负荷优先", "lrb_t10": "加载率均衡"}


@lru_cache(maxsize=1)
def energy_analysis() -> dict:
    from air_station.webapp import data_service as _self
    u = _self.b_unified_summary()
    segs = (u.get("segments") or {})
    out = {"price_cny_per_kwh": PRICE_CNY_PER_KWH,
           "price_note": "电价按工业假设 0.65 元/kWh（非官方口径）", "segments": {}}
    for seg, sv in segs.items():
        # 优先 service_equiv（同供气公平对比），回退 paired_exec / full_window
        scopes = sv.get("scopes") or {}
        scope = "service_equiv" if "service_equiv" in scopes else ("paired_exec" if "paired_exec" in scopes else "full_window")
        data = scopes.get(scope) or {}
        rows = {}
        base = data.get("b1") or {}
        for key in ("b1", "b3_b4", "bal", "lrb_t10"):
            r = data.get(key)
            if not r:
                continue
            E = r.get("E")
            cost = round(E * PRICE_CNY_PER_KWH, 1) if E else None
            rows[key] = {"name": _STRAT_CN.get(key, key), "E_kwh": E, "V_m3": r.get("V"),
                         "SEC": r.get("SEC"), "low_min": r.get("low_min"),
                         "sec_reduction_pct": r.get("sec_reduction_pct"),
                         "saving_same_supply_pct": r.get("saving_same_supply_pct"),
                         "low_reduction_pct": r.get("low_reduction_pct"),
                         "cost_cny": cost}
        out["segments"][seg] = {"scope": scope, "range": sv.get("range"),
                                "n_steps": sv.get("n_steps"), "rows": rows}
    return out


# --------------------------------------------------------------------------- #
# 告警与运行事件
# --------------------------------------------------------------------------- #
_FAULT_MEANING = {26: "弱标签故障码（可能为站级共因，非确认故障）"}


@lru_cache(maxsize=1)
def alarms() -> dict:
    dev_events = pd.read_parquet(PROCESSED / "events.parquet",
                                 columns=["time", "device_id", "running", "fault_code",
                                          "fault_label_quality", "possible_station_level_event"])
    dev_events["time"] = pd.to_datetime(dev_events["time"])
    fault = dev_events[dev_events["fault_code"].fillna(0) > 0].sort_values("time")
    episodes = []
    for d, sub in fault.groupby("device_id"):
        sub = sub.sort_values("time")
        gap = sub["time"].diff() > pd.Timedelta(minutes=5)
        grp = gap.cumsum()
        for _, g in sub.groupby(grp):
            episodes.append({
                "device_id": f"{int(d)}#",
                "start": g["time"].iloc[0].strftime("%Y-%m-%d %H:%M"),
                "end": g["time"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
                "minutes": int(len(g)),
                "code": float(g["fault_code"].iloc[0]),
                "quality": str(g["fault_label_quality"].iloc[0]),
                "station_level": bool(g["possible_station_level_event"].iloc[0]),
            })
    episodes.sort(key=lambda e: e["start"], reverse=True)

    hm = load_metrics().get("health", {}) or {}
    health_alarms = [{"device_id": d, "risk": (hm.get("devices", {}).get(d, {}) or {}).get("risk_level_latest"),
                      "score": (hm.get("devices", {}).get(d, {}) or {}).get("health_score_latest")}
                     for d in ("4#", "5#")]

    surge = _read_json(REPORTS_B / "kpi_surge_events.json", {})
    return {
        "fault_episodes": episodes[:80],
        "fault_total": len(episodes),
        "fault_codes": _FAULT_MEANING,
        "health": health_alarms,
        "surge_stats": {k: (surge.get("valid", {}) or {}).get(k) for k in ("4#", "5#")},
        "surge_note": "喘振为代理分级，无真实标签，误报/漏报不可计算",
    }


# --------------------------------------------------------------------------- #
# 喘振风险监控
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def surge_monitor() -> dict:
    boundary = _read_json(MODELS / "surge_boundary.json", {})
    events = _read_json(REPORTS_B / "kpi_surge_events.json", {})
    curve = {}
    for d, info in (boundary.get("devices") or {}).items():
        knots = info.get("knots", [])
        curve[d] = {"pressure": [k.get("pressure_mpa") for k in knots],
                    "flow": [k.get("flow_m3min") for k in knots]}
    return {
        "boundary_version": boundary.get("boundary_version"),
        "boundary_source": boundary.get("source"),
        "boundary_note": boundary.get("note"),
        "curve": curve,
        "warn_margin": events.get("warn_margin"),
        "stats": {"dev": events.get("dev", {}), "valid": events.get("valid", {})},
        "real_label_note": events.get("real_label_note"),
    }


def surge_at(as_of: str | None = None, mode: str = "b3") -> dict:
    d = run_dispatch(mode, as_of)
    res = d.get("result", {})
    return {"as_of": d.get("as_of"), "mode": mode,
            "surge_status": res.get("surge_status"),
            "safety_blocked": res.get("safety_blocked"),
            "assessment": res.get("surge_assessment")}


# --------------------------------------------------------------------------- #
# Agent 自然语言交互
# --------------------------------------------------------------------------- #
_INTENTS = [
    {"keys": ["省电", "节能", "能效", "最优", "能耗"], "mode": "b3", "intent": "energy_first",
     "reply": "已按能效优先 (B3) 生成调度方案。"},
    {"keys": ["低负荷", "大马拉小车", "空载", "轻载"], "mode": "balanced", "intent": "low_load",
     "reply": "已按低负荷优先策略生成调度方案。"},
    {"keys": ["均衡", "加载率", "平衡"], "mode": "load_balanced", "intent": "load_balance",
     "reply": "已按加载率均衡策略生成调度方案。"},
]


def agent_intents() -> list:
    return [{"intent": i["intent"], "keywords": i["keys"], "mode": i["mode"], "example": i["reply"]}
            for i in _INTENTS]


# --------------------------------------------------------------------------- #
# 知识库封装（供“知识中心 / Agent”调用）
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def knowledge_status() -> dict:
    from air_station.webapp import knowledge_service as KS
    return KS.status()


def knowledge_categories() -> dict:
    from air_station.webapp import knowledge_service as KS
    return KS.categories()


def knowledge_ask(question: str, top_k: int = 5) -> dict:
    from air_station.webapp import knowledge_service as KS
    return KS.ask(question, top_k)


_MODE_CN = {"b3": "能效优先 (B3)", "balanced": "低负荷优先", "load_balanced": "加载率均衡"}


def _dyn_health_text(h: dict) -> str:
    parts = []
    for d in (h or {}).get("devices", []):
        parts.append(f"{d.get('device_id')} 评分 {d.get('health_score')}（{d.get('risk_level')}），主因：{d.get('top_anomaly_factor')}")
    return "；".join(parts) or "无设备健康数据"


def _dyn_dispatch_text(d: dict) -> str:
    r = d.get("result", {})
    combo = " + ".join(r.get("target_combination") or []) or "—"
    return (f"机组组合 {combo}，供气 {r.get('total_flow_m3_min')} m³/min，"
            f"预计功率 {r.get('estimated_power_kw')} kW，喘振 {r.get('surge_status')}，"
            f"执行权限 {r.get('execution_permission')}。")


def _dyn_surge_text(s: dict) -> str:
    return (f"评估时刻 {s.get('as_of')}，整体风险 {s.get('surge_status')}"
            f"{'，已安全阻断' if s.get('safety_blocked') else ''}。（代理分级，非真实喘振线）")


def agent_respond(message: str, as_of: str | None = None) -> dict:
    text = (message or "").strip()
    mode, intent = None, "general"
    for it in _INTENTS:
        if any(k in text for k in it["keys"]):
            mode, intent = it["mode"], it["intent"]
            break

    expl = any(k in text for k in ("为什么", "原因", "解释", "依据", "说明",
                                   "含义", "什么是", "口径", "规则", "是什么"))
    dyn_forecast = any(k in text for k in ("预测", "未来", "负荷", "用气"))
    dyn_health = any(k in text for k in ("健康", "诊断", "异常", "状态", "设备", "振动", "温度"))
    dyn_rul = any(k in text for k in ("剩余寿命", "寿命", "rul", "还能运行", "多久", "剩余时间"))
    dyn_surge = any(k in text for k in ("喘振", "安全", "风险"))
    dyn_dispatch = any(k in text for k in ("调度", "优化", "方案", "机组", "组合"))

    is_dynamic = bool(mode) or dyn_forecast or dyn_health or dyn_rul or dyn_surge or dyn_dispatch

    # 概念型提问（是什么/为什么/含义/口径…）优先走知识库，除非带有明确动作指令
    concept_q = any(k in text for k in ("什么是", "是什么", "是什么意思", "的含义", "含义",
                                        "定义", "口径", "为什么", "原因", "解释"))
    current_marker = any(k in text for k in ("当前", "现在", "此刻", "实时", "4#", "5#", "机组", "这台", "本次"))
    action = any(k in text for k in ("请", "帮我", "生成", "执行", "优化", "调整", "方案"))
    if (concept_q and not action) or (expl and not current_marker and not mode):
        kb = knowledge_ask(text)
        return {"intent": "knowledge", "mode": None, "answer_type": "knowledge",
                "reply": kb.get("answer"), "citations": kb.get("citations", []),
                "knowledge": kb, "sections": []}

    if not is_dynamic:
        kb = knowledge_ask(text)
        return {"intent": "knowledge", "mode": None, "answer_type": "knowledge",
                "reply": kb.get("answer"), "citations": kb.get("citations", []),
                "knowledge": kb, "sections": []}

    payload, sections = {}, []
    if dyn_forecast:
        payload["forecast"] = run_forecast()
    if dyn_health:
        payload["health"] = run_health()
    if dyn_rul:
        payload["rul"] = run_rul()
    if dyn_surge:
        payload["surge"] = surge_at(as_of)
    if mode or dyn_dispatch:
        use_mode = mode or "b3"
        d = run_dispatch(use_mode, as_of)
        payload["dispatch"] = d
        if mode is None:
            mode = use_mode

    if dyn_health and "health" in payload:
        sections.append({"title": "当前状态", "text": _dyn_health_text(payload["health"])})
    elif dyn_forecast and "forecast" in payload:
        preds = payload["forecast"].get("predictions", [])
        sections.append({"title": "当前状态",
                         "text": "；".join(f"{p['horizon_minutes']}分钟 {p['predicted_flow']} m³/min" for p in preds) or "无预测数据"})
    if "dispatch" in payload:
        sections.append({"title": "调度建议", "text": _dyn_dispatch_text(payload["dispatch"])})
    if dyn_surge and "surge" in payload:
        sections.append({"title": "安全检查", "text": _dyn_surge_text(payload["surge"])})

    answer_type = "realtime"
    blocked = bool((payload.get("surge") or {}).get("safety_blocked") or
                   (payload.get("dispatch") or {}).get("result", {}).get("safety_blocked"))
    if blocked:
        answer_type = "safety"

    citations = []
    if expl:
        kb = knowledge_ask(text)
        citations = kb.get("citations", [])
        sections.append({"title": "决策解释",
                         "text": kb.get("answer") if kb.get("status") == "success" else kb.get("answer")})
        answer_type = "combined"
        payload["knowledge"] = kb

    reply = sections[0]["text"] if sections else "已生成结果。"
    return {"intent": intent, "mode": mode, "answer_type": answer_type, "reply": reply,
            "sections": sections, "citations": citations, **payload}


# --------------------------------------------------------------------------- #
# 典型场景一键演示
# --------------------------------------------------------------------------- #
SCENARIO_LABELS = {
    "normal": "正常负荷", "low": "低负荷 / 大马拉小车", "peak": "高峰近容量",
    "step": "负荷突增", "fault": "设备异常 (5# 失效)",
}


def scenario_demo(name: str) -> dict:
    s = sim_summary()
    scen = (s.get("scenarios") or {}).get(name)
    if not scen:
        raise ValueError(f"未知场景: {name}")
    series = {st: sim_series(name, st) for st in ("b1", "b3")}
    b1, b3 = scen.get("b1", {}), scen.get("b3", {})
    return {
        "scenario": name, "label": SCENARIO_LABELS.get(name, name),
        "note": s.get("note"),
        "strategies": {k: scen[k] for k in scen},
        "series": series,
        "comparison": {
            "sec_reduction_pct": b3.get("sec_reduction_vs_b1_pct"),
            "pressure_compliance_b1": b1.get("pressure_compliance"),
            "pressure_compliance_b3": b3.get("pressure_compliance"),
            "pressure_std_reduction_pct": b3.get("pressure_std_reduction_vs_b1_pct"),
            "low_reduction_pct": b3.get("low_reduction_vs_b1_pct"),
            "energy_kwh_b1": b1.get("energy_kwh"), "energy_kwh_b3": b3.get("energy_kwh"),
            "cost_b1": round((b1.get("energy_kwh") or 0) * PRICE_CNY_PER_KWH, 1),
            "cost_b3": round((b3.get("energy_kwh") or 0) * PRICE_CNY_PER_KWH, 1),
        },
    }


# --------------------------------------------------------------------------- #
# 控制确认与执行日志（内存态，仅演示）
# --------------------------------------------------------------------------- #
_CONTROL_LOG: list = []


def control_log() -> list:
    return list(reversed(_CONTROL_LOG))


@lru_cache(maxsize=1)
def acceptance() -> dict:
    return _read_json(ROOT / "analysis" / "reports" / "acceptance" / "TEST_RESULTS.json", {"available": False})


def control_decide(mode: str, as_of: str | None, action: str, note: str = "") -> dict:
    import datetime as _dt
    if action not in ("confirm", "reject", "fallback"):
        raise ValueError("action 必须为 confirm/reject/fallback")
    entry = {
        "seq": len(_CONTROL_LOG) + 1,
        "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode, "as_of": as_of, "action": action, "note": note,
        "permission": "simulation_candidate_only",
        "operator": "演示用户",
    }
    _CONTROL_LOG.append(entry)
    return entry


def prewarm() -> None:
    """后台预热缓存（首次请求更快）。"""
    for fn in (overview, station_series, run_forecast, run_health, run_rul,
               dispatch_times, sim_summary, alarms, surge_monitor, energy_analysis,
               knowledge_status):
        try:
            fn()
        except Exception:
            pass
