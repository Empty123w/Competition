"""B1 测试构造器 (供 conftest 与各测试模块复用)。"""
from __future__ import annotations

import copy
from pathlib import Path

from air_station.baseline import rules
from air_station.optimization.contracts import (
    DeviceSnapshot,
    DispatchConstraints,
    ForecastResult,
    PolicyState,
    StationSnapshot,
)

AS_OF = "2026-09-11T00:00:00"
CONFIG_PATH = Path("configs/baseline.yaml")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_base_config() -> dict:
    return rules.load_config(CONFIG_PATH)


def make_snapshot(pressure=0.60, running=(True, True), f4=250.0, f5=240.0,
                  devices=("4#", "5#")) -> StationSnapshot:
    devs = []
    if "4#" in devices:
        devs.append(DeviceSnapshot("4#", running[0], f4, 1300.0, 0.58, 100.0, 100.0))
    if "5#" in devices:
        devs.append(DeviceSnapshot("5#", running[1], f5, 1280.0, 0.60, 100.0, 100.0))
    if "3#" in devices:
        devs.append(DeviceSnapshot("3#", True, 200.0, 1000.0, 0.60, 100.0, 100.0))
    return StationSnapshot(AS_OF, pressure, devs, data_source="test")


def make_forecast(upper, as_of=AS_OF, horizon=60, predicted=None, lower=None) -> ForecastResult:
    pred = predicted if predicted is not None else upper - 20.0
    lo = lower if lower is not None else upper - 40.0
    return ForecastResult(as_of=as_of, horizon_minutes=horizon,
                          predicted_flow=pred, lower_bound=lo,
                          upper_bound=upper, forecast_id="test-forecast")


def make_state(pressure=0.60, samples=3, last_switch_at=None) -> PolicyState:
    hist = [(f"t{i}", pressure) for i in range(samples)]
    return PolicyState(pressure_history=hist, last_switch_at=last_switch_at or {})


def decide(snapshot, forecast, config, constraints, state=None, health=None):
    return rules.decide(snapshot, forecast, health or [], constraints, config,
                        state if state is not None else make_state(snapshot.header_pressure_mpa))


def constraints_from(config: dict) -> DispatchConstraints:
    return DispatchConstraints.from_config(config)
