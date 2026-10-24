"""FastAPI 应用入口 (阶段 C)。

启动:
    uvicorn air_station.api.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from air_station.api.errors import install_error_handlers
from air_station.api.routers import (agent as agent_router, control as control_router,
                                     knowledge as knowledge_router,
                                     maintenance as maintenance_router,
                                     metrics as metrics_router, models as models_router,
                                     system as system_router)
from air_station.db import base as db_base


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db_base.init_db()
    except Exception:  # noqa: BLE001  数据库不可用不应阻止只读服务
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="空压站多机协同智能调度 Agent - 后端 API",
        version="0.1.0",
        description="C 模块: 后端 API / MCP / Agent / 模拟控制闭环。"
                    "所有控制能力仅作用于模拟 PLC/SCADA/EAM。",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(system_router.router)
    app.include_router(models_router.router)
    app.include_router(control_router.router)
    app.include_router(agent_router.router)
    app.include_router(metrics_router.router)
    app.include_router(maintenance_router.router)
    app.include_router(knowledge_router.router)
    return app


app = create_app()


def run() -> None:
    """控制台入口: air-station-api (默认 0.0.0.0:8000)。"""
    import os
    import uvicorn
    uvicorn.run("air_station.api.app:app",
                host=os.environ.get("AIRSTATION_HOST", "0.0.0.0"),
                port=int(os.environ.get("AIRSTATION_PORT", "8000")))
