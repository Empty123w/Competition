"""知识索引构建 (阶段 K)。

- 读取 knowledge/ 文档, 校验元数据与 source_paths;
- 解析 metric_refs, 读取 artifacts/metrics/*.json 当前数值生成"当前指标值"块;
- TF-IDF(char 2~4gram) 向量化, 保存 vectorizer.pkl / matrix.npz / chunks.json;
- 记录来源文件哈希与 kb_version; 供服务判定 stale。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from air_station.knowledge import chunker, loader, schemas

DEFAULT_CONFIG = "configs/knowledge.yaml"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _get_json_value(obj, dotted: str):
    cur = obj
    for k in dotted.split("."):
        if isinstance(cur, list):
            cur = cur[int(k)]
        elif isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    return cur


def _metric_chunks(docs: list[loader.KnowledgeDoc], repo: Path) -> list[schemas.Chunk]:
    out = []
    for d in docs:
        refs = d.meta.get("metric_refs", []) or []
        if not refs:
            continue
        lines = []
        for r in refs:
            p = repo / r["path"]
            if not p.exists():
                lines.append(f"- {r.get('label', r.get('json_path'))}: 源文件缺失({r['path']})")
                continue
            try:
                val = _get_json_value(json.loads(p.read_text(encoding="utf-8")), r["json_path"])
            except Exception as e:  # noqa
                val = f"读取失败: {e}"
            lines.append(f"- {r.get('label', r.get('json_path'))}: {val}（来源 {r['path']}#{r['json_path']}）")
        out.append(schemas.Chunk(
            knowledge_id=d.meta.get("knowledge_id", ""), doc_title=d.meta.get("title", ""),
            category=d.meta.get("category", ""), section="当前指标值(自动)",
            text="当前指标数值(构建时读取):\n" + "\n".join(lines),
            source_path=d.rel_path, source_paths=list(d.meta.get("source_paths", [])),
            metric_refs=refs))
    return out


def build(config_path: str | Path = DEFAULT_CONFIG, repo_root: str | Path = ".") -> dict:
    cfg = load_config(config_path)
    repo = Path(repo_root)
    root = repo / cfg["knowledge_root"]
    art = repo / cfg["artifact_dir"]; art.mkdir(parents=True, exist_ok=True)

    docs = loader.load_documents(root, repo)
    errors = loader.all_errors(docs)
    if errors:
        raise ValueError(f"知识文档校验失败: {json.dumps(errors, ensure_ascii=False)}")

    chunks: list[schemas.Chunk] = []
    for d in docs:
        chunks += chunker.chunk_document(d)
    chunks += _metric_chunks(docs, repo)

    def _vec_text(c: schemas.Chunk) -> str:
        kw = " ".join(str(k) for k in (c.keywords or []))
        return f"{c.doc_title} {kw} {kw} {c.section} {c.text}"
    texts = [_vec_text(c) for c in chunks]
    from sklearn.feature_extraction.text import TfidfVectorizer
    ng = tuple(cfg["retrieval"]["ngram_range"])
    vec = TfidfVectorizer(analyzer="char", ngram_range=ng, min_df=1, sublinear_tf=True)
    X = vec.fit_transform(texts)

    import joblib
    from scipy import sparse
    joblib.dump(vec, art / "vectorizer.pkl")
    sparse.save_npz(art / "matrix.npz", X)
    (art / "chunks.json").write_text(
        json.dumps([c.to_dict() for c in chunks], ensure_ascii=False, indent=2), encoding="utf-8")

    # 来源哈希: 知识文档 + source_paths + 指标源
    hashes: dict[str, str] = {}
    for d in docs:
        hashes[d.rel_path] = _sha(d.path)
        for sp in d.meta.get("source_paths", []) or []:
            p = repo / sp
            if p.exists() and p.is_file():
                hashes[sp] = _sha(p)
    for r in [c for c in chunks if c.metric_refs]:
        for mr in r.metric_refs:
            p = repo / mr["path"]
            if p.exists():
                hashes[mr["path"]] = _sha(p)

    kb_version = f"{date.today().isoformat().replace('-', '.')}.{_sha(art / 'chunks.json')[:6]}"
    manifest = {
        "kb_version": kb_version,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "retrieval_mode": cfg["retrieval"]["mode"],
        "ngram_range": list(ng),
        "n_docs": len(docs),
        "n_chunks": len(chunks),
        "categories": sorted({c.category for c in chunks}),
        "source_hashes": hashes,
        "config": {"min_score": cfg["retrieval"]["min_score"], "top_k": cfg["retrieval"]["top_k"]},
    }
    (art / "knowledge_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def stale_sources(manifest: dict, repo_root: str | Path = ".") -> list[str]:
    repo = Path(repo_root)
    changed = []
    for rel, h in (manifest.get("source_hashes") or {}).items():
        p = repo / rel
        if not p.exists() or _sha(p) != h:
            changed.append(rel)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="构建知识索引")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()
    m = build(args.config)
    print(f"[OK] kb_version={m['kb_version']} docs={m['n_docs']} chunks={m['n_chunks']} "
          f"categories={len(m['categories'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
