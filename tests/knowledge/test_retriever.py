"""检索测试。"""
from __future__ import annotations
import pytest
from air_station.knowledge.retriever import Retriever


def _r():
    return Retriever("artifacts/knowledge", ".")


def test_hit_expected():
    hits = _r().search("负荷预测使用什么模型", top_k=3, min_score=0.0)
    assert hits and hits[0]["knowledge_id"] == "KB-FORECAST-001"


def test_synonym_and_category_filter():
    r = _r()
    hits = r.search("健康分数怎么算", top_k=5, min_score=0.0)
    assert any(h["knowledge_id"] == "KB-HEALTH-001" for h in hits)
    cat = r.search("指标", category="预测模型", top_k=5, min_score=0.0)
    assert all(h["category"] == "预测模型" for h in cat)


def test_top_k_and_threshold():
    r = _r()
    assert len(r.search("数据", top_k=2, min_score=0.0)) <= 2
    assert r.search("负荷预测", top_k=3, min_score=0.99) == []


def test_errors():
    r = _r()
    with pytest.raises(ValueError):
        r.search("   ")
    with pytest.raises(TypeError):
        r.search(123)  # type: ignore[arg-type]
