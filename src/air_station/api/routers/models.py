"""预测 / 健康 / RUL / 调度 / 喘振 路由 (阶段 C)。"""
from __future__ import annotations

from fastapi import APIRouter

from air_station.api.schemas import DispatchRequest, ForecastRequest
from air_station.db import repository
from air_station.mcp_servers import tools

router = APIRouter(tags=["models"])


@router.post("/api/forecast", summary="用气负荷预测")
def forecast(req: ForecastRequest) -> dict:
    return tools.forecast_load(horizons=req.horizons)


@router.post("/api/health", summary="设备健康诊断")
def health() -> dict:
    return tools.health_diagnose()


@router.post("/api/rul", summary="设备剩余寿命估计(模拟)")
def rul() -> dict:
    return tools.rul_estimation()


@router.get("/api/health/rul", summary="设备剩余寿命估计(模拟)")
def health_rul() -> dict:
    return tools.rul_estimation()


@router.post("/api/dispatch", summary="多机协同调度优化(B)")
def dispatch(req: DispatchRequest) -> dict:
    out = tools.dispatch_optimize(
        mode=req.mode,
        demand_flow_m3_min=req.demand_flow_m3_min,
        horizon_minutes=req.horizon_minutes,
        timeout_ms=req.timeout_ms,
        optimization_config=req.optimization_config)
    if req.persist:
        out["plan_id"] = repository.save_dispatch_plan(out)
    return out


@router.get("/api/dispatch/plans", summary="历史调度方案")
def dispatch_plans(limit: int = 20) -> dict:
    return {"status": "success", "plans": repository.list_dispatch_plans(limit)}


@router.post("/api/surge", summary="喘振风险代理评估(B)")
def surge() -> dict:
    return tools.surge_warning()
