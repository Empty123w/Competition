"""B3 测试构造器。"""
from __future__ import annotations

import copy

from air_station.baseline import rules as B1
from air_station.optimization import contracts as C
from air_station.optimization.contracts import (
    DeviceSnapshot,
    DispatchConstraints,
    ForecastResult,
    HealthAssessment,
    PolicyState,
    RulAssessment,
    StationSnapshot,
)
from air_station.optimization.service import optimize_dispatch

AS_OF = "2026-09-11T00:00:00"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def constraints(**override) -> DispatchConstraints:
    cfg = B1.load_config("configs/baseline.yaml")
    if override:
        cfg = deep_merge(cfg, override)
    return DispatchConstraints.from_config(cfg)


def snapshot(pressure=0.60, running=(True, True), f4=250.0, f5=240.0,
             devices=("4#", "5#"), quality=(True, True)) -> StationSnapshot:
    devs = []
    if "4#" in devices:
        devs.append(DeviceSnapshot("4#", running[0], f4, 1300.0, 0.58, 100.0, 100.0,
                                   quality_valid=quality[0]))
    if "5#" in devices:
        devs.append(DeviceSnapshot("5#", running[1], f5, 1280.0, 0.60, 100.0, 100.0,
                                   quality_valid=quality[1]))
    return StationSnapshot(AS_OF, pressure, devs, data_source="test")


def forecast(upper, as_of=AS_OF, horizon=60) -> ForecastResult:
    return ForecastResult(as_of, horizon, upper - 20.0, upper - 40.0, upper, "test-fc")


def run(snapshot_, forecast_, constraints_, power_model_path, pressure_model_path,
        health=None, state=None, rul=None, config_path="configs/optimization.yaml",
        **override_cfg):
    import json
    from pathlib import Path

    import yaml
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    if override_cfg:
        cfg = deep_merge(cfg, override_cfg)
        tmp = Path("/tmp/opencode") / "b3_test_config.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        config_path = str(tmp)
    return optimize_dispatch(snapshot_, forecast_, health or [], constraints_, state=state,
                             rul=rul, config_path=config_path,
                             power_model_path=power_model_path,
                             pressure_model_path=pressure_model_path)


def health(device_id, risk_level):
    return HealthAssessment(device_id, risk_level)
