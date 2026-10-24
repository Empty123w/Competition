"""切块与索引测试。"""
from __future__ import annotations
import json
from pathlib import Path

from air_station.knowledge import chunker, indexer, loader, schemas


def _doc(body: str) -> loader.KnowledgeDoc:
    meta = {"knowledge_id": "KB-TEST-001", "title": "测试", "category": "测试",
            "keywords": ["测试"], "source_paths": ["configs/knowledge.yaml"],
            "origin": "project_repository", "validation_mode": "real_data",
            "last_verified": "2026-10-24"}
    return loader.KnowledgeDoc(path=Path("x.md"), rel_path="x.md", meta=meta, body=body)


def test_chunk_by_h2_and_table_intact():
    body = "## 结论\n这是结论。\n\n## 表格\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    chunks = chunker.chunk_document(_doc(body))
    assert any(c.section == "结论" for c in chunks)
    tbl = [c for c in chunks if "| a | b |" in c.text]
    assert tbl and "| 3 | 4 |" in tbl[0].text          # 表格不拆散
    for c in chunks:
        assert c.source_path == "x.md" and c.knowledge_id == "KB-TEST-001"


def test_chunk_size_bounded():
    long_para = "。".join(["这是一个很长的段落用于测试切块规则"] * 60)
    chunks = chunker.chunk_document(_doc("## 长章节\n" + long_para))
    assert len(chunks) >= 2
    assert max(len(c.text) for c in chunks) <= 1000


def test_manifest_has_hashes_and_version():
    m = indexer.build("configs/knowledge.yaml")
    assert m["n_docs"] >= 10 and m["n_chunks"] > m["n_docs"]
    assert m["kb_version"]
    assert m["source_hashes"]
    art = Path("artifacts/knowledge")
    assert (art / "vectorizer.pkl").exists() and (art / "matrix.npz").exists()
