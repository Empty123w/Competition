"""设备健康诊断服务: 输出单机最新健康结果。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, Field

from air_station.models.health import evaluate as HE


class IndicatorScore(BaseModel):
    indicator: str
    value: float = 0.0
    zscore: float = 0.0
    contribution: float


class HealthResult(BaseModel):
    number: int
    timestamp: datetime
    health_index: float = Field(..., ge=0, le=100)
    level: str
    top_indicators: list[IndicatorScore] = Field(default_factory=list)
    rul_hours: float | None = None
    method: str = "unsupervised"


def _config(config_path: str) -> dict:
    return yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))


def health_for(number: int, processed_dir: str = "data/processed",
               config_path: str = "configs/health.yaml") -> HealthResult:
    config = _config(config_path)
    series = HE._device_series(processed_dir, number, config)
    last = series.iloc[-1]
    penalties = json.loads(last["rule_penalties"]) if isinstance(last["rule_penalties"], str) else {}
    top = [IndicatorScore(indicator=k, contribution=float(v))
           for k, v in sorted(penalties.items(), key=lambda x: -x[1])[:5]]
    return HealthResult(number=number, timestamp=pd.Timestamp(series.index[-1]).to_pydatetime(),
                        health_index=float(round(last["health_index"], 2)),
                        level=last["level"], top_indicators=top,
                        rul_hours=None,
                        method=config.get("anomaly", {}).get("method", "unsupervised"))


def health_all(processed_dir: str = "data/processed",
               config_path: str = "configs/health.yaml") -> list[HealthResult]:
    config = _config(config_path)
    return [health_for(n, processed_dir, config_path) for n in config.get("devices", [4, 5])]
