"""知识库 min_score 阈值网格标定 (阶段 K)。

用标准问题集测试候选阈值, 记录命中率/拒答率, 输出验收 JSON。
"""
from __future__ import annotations

import json
from pathlib import Path

from air_station.knowledge import indexer
from air_station.knowledge.retriever import Retriever
from air_station.knowledge.service import _no_basis_guard

GRID = [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.15]


def evaluate(config_path: str = "configs/knowledge.yaml",
             questions_path: str = "tests/fixtures/knowledge_questions.json",
             repo_root: str = ".") -> dict:
    cfg = indexer.load_config(config_path)
    qs = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    r = Retriever(Path(repo_root) / cfg["artifact_dir"], repo_root)
    succ = [q for q in qs if q["expected_status"] == "success"]
    na = [q for q in qs if q["expected_status"] == "no_answer"]

    rows = []
    for thr in GRID:
        hit = 0
        for q in succ:
            top = r.search(q["question"], top_k=3, min_score=thr)
            if any(h["knowledge_id"] in q["expected_knowledge_ids"] for h in top):
                hit += 1
        hit_rate = hit / len(succ) if succ else 0
        ref = 0
        for q in na:
            if _no_basis_guard(q["question"]):
                ref += 1
                continue
            top = r.search(q["question"], top_k=1, min_score=thr)
            if not top:
                ref += 1
        ref_rate = ref / len(na) if na else 0
        rows.append({"min_score": thr, "top3_hit_rate": round(hit_rate, 4),
                     "no_answer_rate": round(ref_rate, 4)})

    ok = [x for x in rows if x["top3_hit_rate"] >= 0.85 and x["no_answer_rate"] >= 0.90]
    chosen = (max(ok, key=lambda x: x["min_score"]) if ok
              else max(rows, key=lambda x: x["top3_hit_rate"] + 0.5 * x["no_answer_rate"]))
    return {"grid": rows, "chosen_min_score": chosen["min_score"],
            "chosen": chosen, "n_success": len(succ), "n_no_answer": len(na),
            "selection_rule": "满足 top3_hit>=0.85 且 no_answer>=0.90 的**最大**阈值(避免低阈值造成大量误召); 否则取综合最优"}


def main() -> int:
    res = evaluate()
    out = Path("analysis/reports/phaseK"); out.mkdir(parents=True, exist_ok=True)
    (out / "threshold_tuning.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res["chosen"], ensure_ascii=False))
    print("chosen_min_score =", res["chosen_min_score"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
