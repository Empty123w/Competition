"""知识服务接口测试。"""
from __future__ import annotations
import json
import pytest
from air_station.knowledge import service

SUCCESS_KEYS = {"status", "query", "answer", "confidence", "kb_version",
                "retrieval_mode", "citations", "elapsed_seconds", "kb_status"}


def test_success_schema_and_citations():
    out = service.knowledge_json("负荷预测模型的输入和输出是什么")
    assert out["status"] == "success"
    assert SUCCESS_KEYS <= set(out.keys())
    assert out["citations"], "成功回答必须有引用"
    for c in out["citations"]:
        assert {"knowledge_id", "title", "category", "source_path", "section", "score"} <= set(c.keys())
    json.dumps(out, ensure_ascii=False, allow_nan=False)


def test_no_answer():
    out = service.knowledge_json("某厂家内部故障码999是什么意思")
    assert out["status"] == "no_answer"
    assert out["confidence"] == 0.0 and out["citations"] == []
    json.dumps(out, ensure_ascii=False, allow_nan=False)


def test_search_and_status():
    s = service.search_knowledge("健康分数怎么算")
    assert s["status"] == "success" and s["results"]
    st = service.knowledge_status()
    assert st["index_ok"] and st["n_docs"] >= 10


def test_empty_input():
    with pytest.raises(ValueError):
        service.knowledge_json("  ")
