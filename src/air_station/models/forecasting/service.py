"""用气负荷预测服务 (阶段 A4)。

对外接口(供 C 后端与 B 调度算法对接):

{
  "status": "success",
  "model_type": "LightGBM",
  "prediction_base_time": "2026-09-12T00:00:00",
  "predictions": [
    {"horizon_minutes": 15, "target_time": "...", "predicted_flow": ...,
     "lower_bound": ..., "upper_bound": ...}, ...
  ]
}
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

_CACHE: dict = {}


def _load(model_dir: str, horizon: int):
    import joblib
    key = (model_dir, horizon)
    if key not in _CACHE:
        _CACHE[key] = joblib.load(Path(model_dir) / f"forecast_{horizon}m.pkl")
    return _CACHE[key]


def forecast_json(processed_dir: str = "data/processed",
                  config_path: str = "configs/forecasting.yaml",
                  model_dir: str = "artifacts/models",
                  horizons: list[int] | None = None) -> dict:
    import yaml
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    horizons = horizons or cfg["forecast"]["horizon_minutes"]
    feats = pd.read_parquet(Path(processed_dir) / "forecast_features.parquet")
    last = feats.iloc[[-1]]
    base_time = pd.Timestamp(last["time"].iloc[0])

    t0 = time.perf_counter()
    preds = []
    model_type = "LightGBM"
    for h in horizons:
        packed = _load(model_dir, h)
        model, fcols = packed["model"], packed["feature_cols"]
        model_type = packed.get("model_type", model_type)
        p = float(model.predict(last[fcols])[0])
        preds.append({
            "horizon_minutes": h,
            "target_time": (base_time + timedelta(minutes=h)).isoformat(),
            "predicted_flow": round(p, 2),
            "lower_bound": round(p + packed["ci_lower"], 2),
            "upper_bound": round(p + packed["ci_upper"], 2),
        })
    elapsed = time.perf_counter() - t0
    return {"status": "success", "model_type": model_type,
            "prediction_base_time": base_time.isoformat(),
            "predictions": preds, "elapsed_seconds": round(elapsed, 3)}


if __name__ == "__main__":
    import json
    print(json.dumps(forecast_json(), ensure_ascii=False, indent=2))
