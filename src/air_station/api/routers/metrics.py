"""指标对比路由 (阶段 C): 优化前后与预测/健康指标。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from air_station.db import repository

router = APIRouter(tags=["metrics"])

FORECAST_METRICS = Path("artifacts/metrics/forecast_metrics.json")
HEALTH_METRICS = Path("artifacts/metrics/health_metrics.json")


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/metrics/comparison", summary="优化前后/模型指标对比")
def comparison() -> dict:
    plans = repository.list_dispatch_plans(limit=1)
    latest = plans[0] if plans else None
    return {
        "status": "success",
        "latest_dispatch": latest,
        "forecast_metrics": _load(FORECAST_METRICS),
        "health_metrics": _load(HEALTH_METRICS),
    }
