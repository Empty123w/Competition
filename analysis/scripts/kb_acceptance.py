"""知识库验收指标计算 (阶段 K)。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from air_station.knowledge import indexer, service
from air_station.knowledge.retriever import Retriever


def run(config_path: str = "configs/knowledge.yaml",
        questions_path: str = "tests/fixtures/knowledge_questions.json") -> dict:
    cfg = indexer.load_config(config_path)
    qs = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    succ = [q for q in qs if q["expected_status"] == "success"]
    na = [q for q in qs if q["expected_status"] == "no_answer"]
    r = Retriever(Path(cfg["artifact_dir"]), ".")
    thr = float(cfg["retrieval"]["min_score"])

    hit = 0
    for q in succ:
        top = r.search(q["question"], top_k=3, min_score=thr)
        if any(h["knowledge_id"] in q["expected_knowledge_ids"] for h in top):
            hit += 1
    hit_rate = hit / len(succ)

    cite_ok, js_ok, n_success, lat = 0, 0, 0, []
    for q in succ:
        t0 = time.perf_counter()
        out = service.knowledge_json(q["question"])
        lat.append(time.perf_counter() - t0)
        try:
            json.dumps(out, ensure_ascii=False, allow_nan=False); js_ok += 1
        except Exception:
            pass
        if out["status"] == "success":
            n_success += 1
            if out["citations"]:
                cite_ok += 1

    ref = sum(1 for q in na if service.knowledge_json(q["question"])["status"] == "no_answer")
    lat.sort()
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0

    manifest = json.loads((Path(cfg["artifact_dir"]) / "knowledge_manifest.json").read_text(encoding="utf-8"))
    tun = json.loads(Path("analysis/reports/phaseK/threshold_tuning.json").read_text(encoding="utf-8"))
    return {
        "n_documents": manifest["n_docs"], "n_categories": len(manifest["categories"]),
        "n_chunks": manifest["n_chunks"], "n_questions": len(qs),
        "kb_version": manifest["kb_version"],
        "retrieval": {"mode": cfg["retrieval"]["mode"], "min_score": thr,
                      "min_score_source": "阈值网格标定", "grid": tun["grid"],
                      "selection_rule": tun["selection_rule"]},
        "acceptance": {
            "top3_hit_rate": round(hit_rate, 4),
            "citation_coverage": round(cite_ok / n_success, 4) if n_success else 0.0,
            "no_answer_rate": round(ref / len(na), 4),
            "json_serializable_rate": round(js_ok / len(succ), 4),
            "retrieval_p95_seconds": round(p95, 4),
        },
        "thresholds": {"top3_hit_min": 0.85, "cite_min": 1.0, "no_answer_min": 0.90,
                       "json_min": 1.0, "p95_max_s": 2.0},
    }


def main() -> int:
    res = run()
    out = Path("analysis/reports/phaseK/knowledge_acceptance.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    a = res["acceptance"]
    print(json.dumps(a, ensure_ascii=False))
    ok = (a["top3_hit_rate"] >= 0.85 and a["citation_coverage"] >= 1.0
          and a["no_answer_rate"] >= 0.90 and a["json_serializable_rate"] >= 1.0
          and a["retrieval_p95_seconds"] <= 2.0)
    print("ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
