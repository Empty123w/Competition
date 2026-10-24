"""知识文档加载与元数据解析 (阶段 K)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from air_station.knowledge import schemas


@dataclass
class KnowledgeDoc:
    path: Path
    rel_path: str
    meta: dict
    body: str
    errors: list[str] = field(default_factory=list)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    import yaml
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")
    return {}, text


def load_documents(knowledge_root: str | Path = "knowledge",
                   repo_root: str | Path = ".") -> list[KnowledgeDoc]:
    root = Path(knowledge_root)
    repo = Path(repo_root)
    docs: list[KnowledgeDoc] = []
    for md in sorted(root.rglob("*.md")):
        meta, body = _split_frontmatter(md.read_text(encoding="utf-8"))
        rel = str(md.relative_to(repo)) if str(md).startswith(str(repo)) else str(md)
        errs = schemas.validate_meta(meta, repo)
        docs.append(KnowledgeDoc(path=md, rel_path=rel, meta=meta, body=body, errors=errs))
    return docs


def all_errors(docs: list[KnowledgeDoc]) -> dict[str, list[str]]:
    out = {}
    for d in docs:
        if d.errors:
            out[d.rel_path] = d.errors
    # 重复 knowledge_id
    seen: dict[str, str] = {}
    for d in docs:
        kid = d.meta.get("knowledge_id")
        if not kid:
            continue
        if kid in seen:
            out.setdefault(d.rel_path, []).append(f"knowledge_id 重复: {kid}")
        seen[kid] = d.rel_path
    return out
