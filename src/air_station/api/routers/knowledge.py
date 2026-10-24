"""知识库 API 路由 (阶段 K/C 接入, 新增)。

POST /api/knowledge/search  检索知识块
POST /api/knowledge/ask     完整知识问答
GET  /api/knowledge/status  索引状态(版本/文档/块/stale)
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from air_station.knowledge import service

router = APIRouter(tags=["knowledge"])


class SearchBody(BaseModel):
    query: str = Field(..., description="检索问题")
    top_k: int = Field(5, ge=1, le=20)
    category: str | None = None


class AskBody(BaseModel):
    question: str = Field(..., description="知识问题")
    top_k: int = Field(5, ge=1, le=20)


@router.post("/api/knowledge/search", summary="知识检索")
def search(body: SearchBody) -> dict:
    return service.search_knowledge(body.query, top_k=body.top_k, category=body.category)


@router.post("/api/knowledge/ask", summary="知识问答")
def ask(body: AskBody) -> dict:
    return service.knowledge_json(body.question, top_k=body.top_k)


@router.get("/api/knowledge/status", summary="知识库状态")
def status() -> dict:
    return service.knowledge_status()
