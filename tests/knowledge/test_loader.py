"""知识文档加载与元数据测试。"""
from __future__ import annotations
from pathlib import Path

from air_station.knowledge import loader


def test_load_documents_and_meta():
    docs = loader.load_documents("knowledge", ".")
    assert len(docs) >= 10
    cats = {d.meta.get("category") for d in docs}
    assert len(cats) >= 10
    for d in docs:
        assert not d.errors, f"{d.rel_path}: {d.errors}"
        for k in ("knowledge_id", "title", "category", "keywords", "source_paths",
                  "origin", "validation_mode", "last_verified"):
            assert d.meta.get(k), f"{d.rel_path} 缺少 {k}"


def test_unique_ids_and_sources_exist():
    docs = loader.load_documents("knowledge", ".")
    ids = [d.meta["knowledge_id"] for d in docs]
    assert len(ids) == len(set(ids))
    repo = Path(".")
    for d in docs:
        for sp in d.meta["source_paths"]:
            assert (repo / sp).exists(), f"{d.rel_path} 的 source_path 不存在: {sp}"
