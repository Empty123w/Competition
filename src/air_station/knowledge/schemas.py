"""知识库数据结构与元数据校验 (阶段 K)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_META = ["knowledge_id", "title", "category", "keywords", "source_paths",
                 "origin", "validation_mode", "last_verified"]


@dataclass
class Chunk:
    knowledge_id: str
    doc_title: str
    category: str
    section: str
    text: str
    source_path: str          # 知识文档相对路径
    source_paths: list[str] = field(default_factory=list)   # 元数据来源
    metric_refs: list[dict] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"knowledge_id": self.knowledge_id, "doc_title": self.doc_title,
                "category": self.category, "section": self.section, "text": self.text,
                "source_path": self.source_path, "source_paths": self.source_paths,
                "metric_refs": self.metric_refs, "keywords": self.keywords}


def validate_meta(meta: dict, repo_root: Path) -> list[str]:
    """返回错误列表(空=通过)。"""
    errs = []
    for k in REQUIRED_META:
        if k not in meta or meta[k] in (None, "", []):
            errs.append(f"缺少元数据: {k}")
    for p in meta.get("source_paths", []) or []:
        if not (repo_root / p).exists():
            errs.append(f"source_path 不存在: {p}")
    return errs
