"""Agent 会话路由 (阶段 C)。"""
from __future__ import annotations

from fastapi import APIRouter

from air_station.api.errors import ApiError
from air_station.api.schemas import AgentChatRequest
from air_station.db import repository
from air_station.mcp_servers import tools

router = APIRouter(tags=["agent"])


@router.post("/api/agent/chat", summary="自然语言交互(多轮)")
def chat(req: AgentChatRequest) -> dict:
    return tools.orchestrate(req.message, execute=req.execute,
                             persist=req.persist, config_path="configs/agent.yaml")


@router.get("/api/agent/sessions", summary="会话列表")
def sessions(limit: int = 50) -> dict:
    return {"status": "success", "sessions": repository.list_agent_sessions(limit)}


@router.get("/api/agent/sessions/{session_id}", summary="会话详情")
def session_detail(session_id: str) -> dict:
    s = repository.get_agent_session(session_id)
    if s is None:
        raise ApiError("E_NOT_FOUND", f"会话不存在: {session_id}")
    return {"status": "success", "session": s}
