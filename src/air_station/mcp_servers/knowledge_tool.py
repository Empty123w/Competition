"""知识库 MCP 工具 (阶段 K/C 接入)。

封装 A 的 `knowledge_json()`，暴露为 MCP 工具 `knowledge_search`；
不复制检索算法。属于**新增**能力。
"""
from __future__ import annotations


def knowledge_search(question: str, top_k: int = 5,
                     config_path: str = "configs/knowledge.yaml") -> dict:
    """项目知识检索问答: 返回答案与来源引用; 无依据时拒答。"""
    from air_station.knowledge.service import knowledge_json
    return knowledge_json(question, top_k=top_k, config_path=config_path)
