"""调度集成桥: A 的预测/健康 + C 的设备状态 → B 的 plan_dispatch。

- 输入构造全部走 B 的公共契约与适配器 (contracts / baseline.pipeline);
- 调用 B 的稳定接口 `b_api.plan_dispatch`, 输出原样返回, 仅在 `c_meta` 附加
  C 侧派生信息(是否仿真候选/是否需人工复核/是否可执行/耗时);
- C 不改写 B 的成功字段, 不伪造执行许可。
"""
from __future__ import annotations

import time
from pathlib import Path

from air_station.baseline import pipeline as b_pipeline
from air_station.baseline import rules as b_rules
from air_station.optimization import b_api
from air_station.optimization.contracts import (DispatchConstraints, ForecastResult,
                                                HealthAssessment)

DEFAULT_OPT_CONFIG = "configs/optimization.yaml"
DEFAULT_BASELINE_CONFIG = b_rules.DEFAULT_CONFIG  # configs/baseline.yaml
DEFAULT_PROCESSED = "data/processed"
DEFAULT_FORECAST_CONFIG = "configs/forecasting.yaml"
DEFAULT_MODEL_DIR = "artifacts/models"
DEFAULT_HEALTH_THRESHOLDS = "configs/health_thresholds.yaml"


def build_snapshot(as_of=None, processed_dir: str = DEFAULT_PROCESSED):
    """由处理后的站级/设备级数据构造 B 的 StationSnapshot。"""
    dev, st = b_pipeline.load_processed(processed_dir)
    if dev.empty:
        raise FileNotFoundError(f"[integration] 设备级数据为空: {Path(processed_dir)}")
    if as_of is None:
        as_of = dev["time"].max()
    return b_pipeline.snapshot_from_frames(dev, st, as_of)


def build_forecast(demand_flow_m3_min: float | None = None,
                   horizon_minutes: int = 15,
                   processed_dir: str = DEFAULT_PROCESSED,
                   config_path: str = DEFAULT_FORECAST_CONFIG,
                   model_dir: str = DEFAULT_MODEL_DIR,
                   as_of: str | None = None) -> ForecastResult:
    """构造 B 的 ForecastResult。

    - 默认调用 A 的 forecast_json() 取对应时域预测;
    - 若显式给定 demand_flow_m3_min, 则以其作为需求(上界同为该值)。
    """
    if demand_flow_m3_min is not None:
        d = float(demand_flow_m3_min)
        return ForecastResult(as_of=str(as_of or ""), horizon_minutes=int(horizon_minutes),
                              predicted_flow=d, lower_bound=d, upper_bound=d,
                              forecast_id=f"C-override-{int(horizon_minutes)}m")
    from air_station.models.forecasting.service import forecast_json
    fc = forecast_json(processed_dir=processed_dir, config_path=config_path,
                       model_dir=model_dir)
    preds = fc["predictions"]
    chosen = min(preds, key=lambda p: abs(p["horizon_minutes"] - horizon_minutes))
    return ForecastResult(
        as_of=fc["prediction_base_time"],
        horizon_minutes=int(chosen["horizon_minutes"]),
        predicted_flow=float(chosen["predicted_flow"]),
        lower_bound=float(chosen["lower_bound"]),
        upper_bound=float(chosen["upper_bound"]),
        forecast_id=f"A-live-{chosen['horizon_minutes']}m-{fc['prediction_base_time']}")


def build_health(processed_dir: str = DEFAULT_PROCESSED,
                 thresholds_path: str = DEFAULT_HEALTH_THRESHOLDS) -> list[HealthAssessment]:
    from air_station.models.health.service import health_json
    h = health_json(processed_dir=processed_dir, thresholds_path=thresholds_path)
    return [HealthAssessment(device_id=d["device_id"], risk_level=d["risk_level"],
                             health_score=d.get("health_score"),
                             suggestion=d.get("suggestion", ""), source="unsupervised")
            for d in h.get("devices", [])]


def build_constraints(config_path: str = DEFAULT_OPT_CONFIG,
                      baseline_config: str = DEFAULT_BASELINE_CONFIG) -> DispatchConstraints:
    """由 B 的配置构造 DispatchConstraints (阈值全部外部化)。"""
    import yaml
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"[integration] 优化配置缺失: {p}")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg.setdefault("constraints", {})
    return DispatchConstraints.from_config(cfg)


def dispatch_plan(mode: str = "b3",
                  demand_flow_m3_min: float | None = None,
                  horizon_minutes: int = 15,
                  timeout_ms: float | None = None,
                  as_of=None,
                  processed_dir: str = DEFAULT_PROCESSED,
                  forecasting_config: str = DEFAULT_FORECAST_CONFIG,
                  model_dir: str = DEFAULT_MODEL_DIR,
                  health_thresholds: str = DEFAULT_HEALTH_THRESHOLDS,
                  optimization_config: str = DEFAULT_OPT_CONFIG) -> dict:
    """调用 B 的统一调度接口, 返回 B 的输出 + C 侧元信息。"""
    t0 = time.perf_counter()
    snapshot = build_snapshot(as_of=as_of, processed_dir=processed_dir)
    forecast = build_forecast(demand_flow_m3_min, horizon_minutes, processed_dir,
                              forecasting_config, model_dir, as_of=snapshot.as_of)
    health = build_health(processed_dir, health_thresholds)
    constraints = build_constraints(optimization_config)
    out = b_api.plan_dispatch(mode, snapshot, forecast, health, constraints,
                              timeout_ms=timeout_ms)
    out.setdefault("mode", mode)
    out["c_meta"] = {
        "candidate_usable": b_api.candidate_usable(out),
        "needs_human_review": b_api.needs_human_review(out),
        "is_success": b_api.is_success(out),
        "is_fallback": b_api.is_fallback_result(out),
        "is_blocked": b_api.is_blocked(out),
        "is_infeasible": b_api.is_infeasible(out),
        "execution_allowed": b_api.execution_allowed(out),
        "api_version": b_api.API_VERSION,
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
    }
    return out
