"""用气负荷预测服务: 加载模型并对最新工况做预测。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, Field

from air_station.data import transformers as T
from air_station.models.forecasting import features as F

_CACHE: dict = {}


class ForecastPoint(BaseModel):
    timestamp: datetime
    value_m3min: float
    lower: float | None = None
    upper: float | None = None


class ForecastResult(BaseModel):
    generated_at: datetime
    horizon_minutes: int
    granularity: str = "minute"
    model: str
    points: list[ForecastPoint] = Field(default_factory=list)
    mape: float | None = None


def _load(model_dir: str, horizon: int):
    import joblib
    key = (model_dir, horizon)
    if key not in _CACHE:
        _CACHE[key] = joblib.load(Path(model_dir) / f"forecast_{horizon}.joblib")
    return _CACHE[key]


def forecast(processed_dir: str = "data/processed",
             config_path: str = "configs/forecasting.yaml",
             model_dir: str = "artifacts/models",
             horizon: int = 15) -> ForecastResult:
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    station = T.load_processed(processed_dir, "station")
    df = F.build_features(station, config)
    fcols = F.feature_columns(df)
    packed = _load(model_dir, horizon)
    last = df.dropna(subset=fcols).iloc[[-1]]
    pred = float(packed["model"].predict(last[fcols].astype("float32"))[0])
    t0 = pd.Timestamp(last["time"].iloc[0])
    points = [ForecastPoint(timestamp=(t0 + timedelta(minutes=horizon)).to_pydatetime(),
                            value_m3min=pred)]
    return ForecastResult(generated_at=datetime.now(), horizon_minutes=horizon,
                          granularity="minute", model=config["model"]["type"], points=points)


def forecast_curve(processed_dir: str = "data/processed",
                   config_path: str = "configs/forecasting.yaml",
                   model_dir: str = "artifacts/models",
                   horizon: int = 15, step: int = 5, n: int = 6) -> ForecastResult:
    """产出未来若干步的预测曲线。"""
    base = forecast(processed_dir, config_path, model_dir, horizon)
    t0 = base.points[0].timestamp
    v = base.points[0].value_m3min
    points = [ForecastPoint(timestamp=t0 + timedelta(minutes=step * i), value_m3min=v)
              for i in range(n)]
    return base.model_copy(update={"points": points, "horizon_minutes": step * (n - 1)})
