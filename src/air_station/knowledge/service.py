"""知识库服务接口 (阶段 K)。

对外稳定入口:
  search_knowledge(query, top_k=5, category=None) -> dict
  knowledge_json(question, top_k=5) -> dict
  knowledge_status() -> dict
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from air_station.knowledge import indexer
from air_station.knowledge.retriever import Retriever

DEFAULT_CONFIG = "configs/knowledge.yaml"


def _cfg(config_path: str | Path = DEFAULT_CONFIG) -> dict:
    return indexer.load_config(config_path)


def _retriever(cfg: dict, repo_root: str) -> Retriever:
    return Retriever(Path(repo_root) / cfg["artifact_dir"], repo_root)


def _kb_status(manifest: dict, repo_root: str) -> dict:
    changed = indexer.stale_sources(manifest, repo_root)
    return {"kb_status": "stale" if changed else "fresh", "stale_sources": changed}


_NO_BASIS_MARKERS = ["厂家", "供应商", "外部系统", "内部故障码"]


def _no_basis_guard(question: str) -> bool:
    """仓库无厂家手册/外部系统资料, 命中即拒答。"""
    return any(m in question for m in _NO_BASIS_MARKERS)


def search_knowledge(query: str, top_k: int = 5, category: str | None = None,
                     config_path: str | Path = DEFAULT_CONFIG,
                     repo_root: str = ".") -> dict:
    t0 = time.perf_counter()
    cfg = _cfg(config_path)
    r = _retriever(cfg, repo_root)
    min_score = float(cfg["retrieval"]["min_score"])
    hits = r.search(query, top_k=top_k, category=category, min_score=min_score)
    st = _kb_status(r.manifest, repo_root)
    return {
        "status": "success", "query": query, "retrieval_mode": cfg["retrieval"]["mode"],
        "kb_version": r.kb_version(), **st,
        "results": [{"knowledge_id": h["knowledge_id"], "title": h["doc_title"],
                     "category": h["category"], "section": h["section"], "score": h["score"],
                     "source_path": h["source_path"], "text": h["text"]} for h in hits],
        "elapsed_seconds": round(time.perf_counter() - t0, 4),
    }


def _compose_answer(hits: list[dict]) -> str:
    if not hits:
        return ""
    top = hits[0]
    text = top["text"]
    # 优先使用"结论"段
    for h in hits:
        if h["section"].startswith("结论"):
            text = h["text"]
            break
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("|")]
    body = " ".join(lines)[:400]
    return f"根据项目当前实现（{top['doc_title']}）：{body}"


def knowledge_json(question: str, top_k: int = 5, config_path: str | Path = DEFAULT_CONFIG,
                   repo_root: str = ".") -> dict:
    t0 = time.perf_counter()
    if not isinstance(question, str) or not question.strip():
        raise ValueError("[knowledge] question 必须为非空字符串")
    cfg = _cfg(config_path)
    r = _retriever(cfg, repo_root)
    if _no_basis_guard(question):
        return {"status": "no_answer", "query": question, "answer": cfg["answer"]["no_answer_text"],
                "confidence": 0.0, "citations": [], "kb_version": r.kb_version(),
                "retrieval_mode": cfg["retrieval"]["mode"], "kb_status": _kb_status(r.manifest, repo_root)["kb_status"],
                "stale_sources": [], "elapsed_seconds": round(time.perf_counter()-t0, 4)}
    min_score = float(cfg["retrieval"]["min_score"])
    hits = r.search(question, top_k=top_k, min_score=min_score)
    st = _kb_status(r.manifest, repo_root)
    meta = {"query": question, "kb_version": r.kb_version(),
            "retrieval_mode": cfg["retrieval"]["mode"], **st}
    if not hits:
        return {"status": "no_answer", "answer": cfg["answer"]["no_answer_text"],
                "confidence": 0.0, "citations": [],
                "elapsed_seconds": round(time.perf_counter() - t0, 4), **meta}
    answer = _compose_answer(hits)
    if st["kb_status"] == "stale":
        answer = "（提示：知识库索引已过期，来源文件已变更，以下数值可能非最新。）" + answer
    citations = [{"knowledge_id": h["knowledge_id"], "title": h["doc_title"],
                  "category": h["category"], "source_path": h["source_path"],
                  "section": h["section"], "score": h["score"]}
                 for h in hits[:int(cfg["answer"]["max_citations"])]]
    return {"status": "success", "answer": answer, "confidence": hits[0]["score"],
            "citations": citations,
            "elapsed_seconds": round(time.perf_counter() - t0, 4), **meta}


def knowledge_status(config_path: str | Path = DEFAULT_CONFIG, repo_root: str = ".") -> dict:
    cfg = _cfg(config_path)
    r = _retriever(cfg, repo_root)
    r._ensure()
    st = _kb_status(r.manifest, repo_root)
    return {"status": "success", "kb_version": r.kb_version(),
            "n_docs": r.manifest.get("n_docs"), "n_chunks": r.manifest.get("n_chunks"),
            "categories": r.manifest.get("categories"),
            "built_at": r.manifest.get("built_at"), **st, "index_ok": True}


def _cli_answer(question: str, config_path: str, top_k: int) -> dict:
    return knowledge_json(question, top_k=top_k, config_path=config_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库问答")
    ap.add_argument("--question", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()
    if args.question:
        import json
        print(json.dumps(_cli_answer(args.question, args.config, args.top_k),
                         ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    print("[knowledge] 请用 --question 提问, 或先用 indexer --build 构建索引")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
