"""B2 测试 fixtures: 保证模型工件存在 (缺失时训练一次), 避免关键测试被跳过。"""
from __future__ import annotations

from pathlib import Path

import pytest

POWER_MODEL = Path("artifacts/models/power_model.json")
PRESSURE_MODEL = Path("artifacts/models/pressure_model.json")


@pytest.fixture(scope="session")
def power_model_path() -> str:
    if not POWER_MODEL.exists():
        from air_station.optimization import performance
        performance.train_performance()
    return str(POWER_MODEL)


@pytest.fixture(scope="session")
def pressure_model_path() -> str:
    if not PRESSURE_MODEL.exists():
        from air_station.optimization import pressure
        pressure.train_pressure()
    return str(PRESSURE_MODEL)
