"""知识库测试夹具: 确保索引存在。"""
from __future__ import annotations
from pathlib import Path
import pytest

from air_station.knowledge import indexer


@pytest.fixture(scope="session", autouse=True)
def _ensure_index():
    art = Path("artifacts/knowledge")
    if not (art / "chunks.json").exists():
        indexer.build("configs/knowledge.yaml")
    yield
