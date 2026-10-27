"""可视化页面 Web 服务（FastAPI）。

启动:
    python -m air_station.webapp.app
或:
    uvicorn air_station.webapp.app:app --host 0.0.0.0 --port 8080

根路径 "/" 直接返回可视化首页；所有数据通过 /api/* 暴露，
每个接口都调用仓库中已实现的公开入口或读取已产出的离线产物。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from air_station.webapp import data_service as DS

ROOT = DS.ROOT
STATIC = Path(__file__).resolve().parent / "static"

# 保证配置/模型/数据的相对路径都能解析到仓库根目录
os.chdir(ROOT)

app = FastAPI(title="空压站多机协同智能调度可视化", version="1.0.0")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/overview")
def api_overview():
    return DS.overview()


@app.get("/api/station")
def api_station():
    return DS.station_series()


@app.get("/api/device/{device}")
def api_device(device: str):
    return DS.device_series(device)


@app.get("/api/forecast")
def api_forecast(horizon: int = Query(15, description="15/30/60")):
    return {
        "live": DS.run_forecast(),
        "metrics": DS.load_metrics().get("forecast", {}),
        "rolling": DS.load_metrics().get("forecast_rolling", {}),
        "meta": DS.forecast_meta(),
        "series": DS.forecast_series(horizon),
    }


@app.get("/api/health")
def api_health():
    return {
        "live": DS.run_health(),
        "metrics": DS.load_metrics().get("health", {}),
        "history": {d: DS.health_history_series(d) for d in ("4#", "5#")},
    }


@app.get("/api/rul")
def api_rul():
    return {
        "live": DS.run_rul(),
        "metrics": DS.load_metrics().get("rul", {}),
        "meta": DS.rul_meta(),
        "history": {d: DS.rul_history_series(d) for d in ("4#", "5#")},
    }


@app.get("/api/dispatch/times")
def api_dispatch_times():
    return {"times": DS.dispatch_times()}


@app.get("/api/dispatch")
def api_dispatch(mode: str = Query("b3"), as_of: str | None = None):
    if mode not in ("b3", "balanced", "load_balanced"):
        raise HTTPException(status_code=400, detail=f"mode 必须为 b3/balanced/load_balanced, got {mode}")
    try:
        return DS.run_dispatch(mode, as_of)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/dispatch/compare")
def api_dispatch_compare(as_of: str | None = None):
    out = {}
    for mode in ("b3", "balanced", "load_balanced"):
        try:
            out[mode] = DS.run_dispatch(mode, as_of)
        except Exception as exc:  # noqa
            out[mode] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
    return out


@app.get("/api/simulation")
def api_simulation():
    return {
        "summary": DS.sim_summary(),
        "load_ratio": DS.load_ratio(),
        "series": {s: {sc: DS.sim_series(sc, s) for sc in ("normal", "low", "peak", "step", "fault")}
                   for s in ("b1", "b3")},
    }


@app.get("/api/simulation/run")
def api_simulation_run(strategy: str = Query("b3"), scenario: str = Query("normal")):
    if strategy not in ("b1", "b3", "balanced", "load_balanced"):
        raise HTTPException(status_code=400, detail="strategy 必须为 b1/b3/balanced/load_balanced")
    try:
        return DS.run_simulation_live(strategy, scenario)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/delivery")
def api_delivery():
    return {
        "call_examples": DS.call_examples(),
        "unified_summary": DS.b_unified_summary(),
        "load_ratio": DS.load_ratio(),
        "data_manifest": DS.data_manifest(),
    }


@app.get("/api/healthz")
def healthz():
    return JSONResponse({"status": "ok", "root": str(ROOT)})


# ---------------------- 机组实时状态 / 模拟播放 ---------------------- #
@app.get("/api/rt/state")
def api_rt_state(as_of: str | None = None):
    return DS.rt_state(as_of)


@app.get("/api/rt/frame")
def api_rt_frame(i: int = Query(0, ge=0)):
    times = DS.playback_times()
    if not times:
        raise HTTPException(status_code=503, detail="无可用播放时刻")
    idx = min(i, len(times) - 1)
    return {"index": idx, "total": len(times), "as_of": times[idx],
            "state": DS.rt_state(times[idx], history_minutes=120, with_reco=False)}


# ---------------------- 能效与成本 ---------------------- #
@app.get("/api/energy")
def api_energy():
    return DS.energy_analysis()


# ---------------------- 告警与事件 ---------------------- #
@app.get("/api/alarms")
def api_alarms():
    return DS.alarms()


# ---------------------- 喘振风险 ---------------------- #
@app.get("/api/surge")
def api_surge(as_of: str | None = None, mode: str = "b3"):
    return {"monitor": DS.surge_monitor(), "current": DS.surge_at(as_of, mode)}


# ---------------------- Agent 自然语言交互 ---------------------- #
class ChatBody(BaseModel):
    message: str
    as_of: str | None = None


@app.get("/api/agent/intents")
def api_agent_intents():
    return {"intents": DS.agent_intents()}


@app.post("/api/agent/chat")
def api_agent_chat(body: ChatBody):
    try:
        return DS.agent_respond(body.message, body.as_of)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


# ---------------------- 典型场景一键演示 ---------------------- #
@app.get("/api/scenario/{name}")
def api_scenario(name: str):
    try:
        return DS.scenario_demo(name)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


# ---------------------- 控制确认与执行日志 ---------------------- #
class ControlBody(BaseModel):
    mode: str
    as_of: str | None = None
    action: str
    note: str = ""


@app.get("/api/control/log")
def api_control_log():
    return {"log": DS.control_log()}


@app.post("/api/control/decide")
def api_control_decide(body: ControlBody):
    try:
        return DS.control_decide(body.mode, body.as_of, body.action, body.note)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/acceptance")
def api_acceptance():
    return DS.acceptance()


# ---------------------- 知识库 ---------------------- #
class AskBody(BaseModel):
    question: str
    top_k: int = 5


@app.get("/api/knowledge/status")
def api_knowledge_status():
    return DS.knowledge_status()


@app.get("/api/knowledge/categories")
def api_knowledge_categories():
    return DS.knowledge_categories()


@app.post("/api/knowledge/ask")
def api_knowledge_ask(body: AskBody):
    try:
        return DS.knowledge_ask(body.question, body.top_k)
    except Exception as exc:  # noqa
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


# ---------------------- 新版前端 v2 + C 后端反代 ---------------------- #
CAPI_BASE = os.environ.get("CAPI_BASE", "http://127.0.0.1:8000")


@app.get("/v2", include_in_schema=False)
def v2_index():
    return FileResponse(STATIC / "v2" / "index.html")


@app.api_route("/capi/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def capi_proxy(path: str, request: Request):
    """把 /capi/* 同源转发到 C 后端（默认 127.0.0.1:8000），避免跨域。"""
    import requests
    from starlette.concurrency import run_in_threadpool

    url = f"{CAPI_BASE.rstrip('/')}/{path}"
    body = await request.body()
    headers = {}
    ct = request.headers.get("content-type")
    if ct:
        headers["content-type"] = ct
    try:
        resp = await run_in_threadpool(
            requests.request, request.method, url,
            params=dict(request.query_params), data=body,
            headers=headers or {"content-type": "application/json"}, timeout=90)
    except Exception as exc:  # noqa
        return JSONResponse({"status": "error",
                             "error": {"code": "E_GATEWAY", "message": f"后端不可用: {type(exc).__name__}"}},
                            status_code=502)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
