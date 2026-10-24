"""知识文档切块 (阶段 K)。

规则:
  - 按 Markdown 二级标题(##)切块;
  - 表格(| 开头)作为整体, 不拆散;
  - 单块约 200~800 中文字符;
  - 超长章节按三级标题(###)或自然段继续切分;
  - 每个块保留 knowledge_id / 标题 / 分类 / 章节 / 来源。
"""
from __future__ import annotations

from air_station.knowledge import loader, schemas

MIN_CHARS = 200
MAX_CHARS = 800


def _is_table(line: str) -> bool:
    return line.lstrip().startswith("|")


def _blocks(lines: list[str]) -> list[tuple[str, str]]:
    """把行分成 (kind, text) 块; kind ∈ {table, text}。"""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    kind = "text"

    import re

    def _split_long(text: str) -> list[str]:
        if len(text) <= MAX_CHARS:
            return [text]
        sents = re.split(r"(?<=[。！？；\n])", text)
        parts, cur = [], ""
        for s in sents:
            if not s:
                continue
            if len(cur) + len(s) > MAX_CHARS and cur:
                parts.append(cur); cur = s
            else:
                cur += s
        if cur:
            parts.append(cur)
        return parts

    def flush():
        if buf:
            text = "\n".join(buf).strip()
            if kind == "text" and len(text) > MAX_CHARS:
                for piece in _split_long(text):
                    out.append(("text", piece))
            else:
                out.append((kind, text))

    for ln in lines:
        k = "table" if _is_table(ln) else "text"
        if k != kind:
            flush(); buf = [ln]; kind = k
        else:
            buf.append(ln)
        # 文本块遇空行分段
        if k == "text" and ln.strip() == "":
            flush(); buf = []; kind = "text"
    flush()
    return [(k, t) for k, t in out if t]


def _pack(blocks: list[tuple[str, str]]) -> list[str]:
    """把块打包为 200~800 字的片段; 表格块整体保留。"""
    chunks: list[str] = []
    cur = ""
    for k, t in blocks:
        if k == "table" or len(t) > MAX_CHARS:
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(t)                      # 表格/超长整体成块
            continue
        if len(cur) + len(t) + 1 > MAX_CHARS and len(cur) >= MIN_CHARS:
            chunks.append(cur); cur = t
        else:
            cur = f"{cur}\n{t}".strip() if cur else t
    if cur:
        chunks.append(cur)
    return chunks


def _sections(body: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    title = "正文"
    buf: list[str] = []
    for ln in body.splitlines():
        if ln.startswith("## "):
            if buf:
                sections.append((title, buf))
            title = ln[3:].strip(); buf = []
        else:
            buf.append(ln)
    if buf:
        sections.append((title, buf))
    return sections


def chunk_document(doc: loader.KnowledgeDoc) -> list[schemas.Chunk]:
    meta = doc.meta
    refs = meta.get("metric_refs", []) or []
    out: list[schemas.Chunk] = []
    for title, lines in _sections(doc.body):
        pieces = _pack(_blocks(lines))
        for i, piece in enumerate(pieces):
            if not piece.strip():
                continue
            section = title if len(pieces) == 1 else f"{title}({i + 1})"
            out.append(schemas.Chunk(
                knowledge_id=meta.get("knowledge_id", ""), doc_title=meta.get("title", ""),
                category=meta.get("category", ""), section=section, text=piece,
                source_path=doc.rel_path, source_paths=list(meta.get("source_paths", [])),
                metric_refs=refs, keywords=list(meta.get("keywords", []) or [])))
    return out
