"""传统人工规则调度基线 (阶段 B1)。

对外主要入口:
  - rules.decide(snapshot, forecast, health, constraints, config, state)
  - rules.load_config(path)
  - pipeline.run_baseline(...) / run_baseline_from_frames(...)
  - evaluator.observed_metrics(...) / baseline_metrics(...)

基线是**对照算法**, 与 B3 优化器使用同一 snapshot/forecast/health/constraints。
"""
from air_station.baseline import evaluator, pipeline, rules  # noqa: F401

__all__ = ["rules", "evaluator", "pipeline"]
