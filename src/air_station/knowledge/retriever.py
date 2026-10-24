"""知识检索 (阶段 K): TF-IDF(char n-gram) + 余弦相似度。"""
from __future__ import annotations

import json
from pathlib import Path


class Retriever:
    def __init__(self, artifact_dir: str | Path = "artifacts/knowledge",
                 repo_root: str | Path = "."):
        self.artifact_dir = Path(artifact_dir)
        self.repo = Path(repo_root)
        self._loaded = False

    def _ensure(self):
        if self._loaded:
            return
        import joblib
        from scipy import sparse
        art = self.artifact_dir
        for f in ("vectorizer.pkl", "matrix.npz", "chunks.json"):
            if not (art / f).exists():
                raise FileNotFoundError(
                    f"[knowledge] 索引缺失: {art / f} (先运行 knowledge.indexer --build)")
        try:
            self.vec = joblib.load(art / "vectorizer.pkl")
            self.matrix = sparse.load_npz(art / "matrix.npz")
            self.chunks = json.loads((art / "chunks.json").read_text(encoding="utf-8"))
            self.manifest = json.loads((art / "knowledge_manifest.json").read_text(encoding="utf-8"))
        except Exception as e:  # noqa
            raise RuntimeError(f"[knowledge] 索引损坏: {e}") from e
        if len(self.chunks) != self.matrix.shape[0]:
            raise RuntimeError("[knowledge] 索引不一致: chunks 与 matrix 行数不符")
        self._loaded = True

    def search(self, query: str, top_k: int = 5, category: str | None = None,
               min_score: float = 0.0) -> list[dict]:
        if not isinstance(query, str):
            raise TypeError("[knowledge] query 必须为字符串")
        if not query.strip():
            raise ValueError("[knowledge] query 为空")
        self._ensure()
        from sklearn.metrics.pairwise import cosine_similarity
        qv = self.vec.transform([query])
        sims = cosine_similarity(qv, self.matrix)[0]
        order = sims.argsort()[::-1]
        out = []
        for i in order:
            c = self.chunks[i]
            if category and c.get("category") != category:
                continue
            score = float(sims[i])
            if score < min_score:
                continue
            out.append({**c, "score": round(score, 4)})
            if len(out) >= top_k:
                break
        return out

    def kb_version(self) -> str:
        self._ensure()
        return self.manifest.get("kb_version", "unknown")
