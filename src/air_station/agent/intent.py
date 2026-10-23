"""规则意图引擎 (阶段 C)。

离线可测的确定性关键词/模式匹配; 配置 LLM 时可由 llm.classify 覆盖。
"""
from __future__ import annotations

import re

# 意图关键词(顺序即优先级: 越靠前越优先)
_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("control_execute", ["下发", "执行", "启停", "开机", "停机", "启动", "关闭机组", "加载指令"]),
    ("workorder", ["工单", "派工", "维修", "维保", "保养"]),
    ("surge_query", ["喘振", "危险区", "逼近边界"]),
    ("rul_query", ["剩余寿命", "还能用多久", "寿命", "rul", "退化"]),
    ("dispatch_optimize", ["调度", "优化", "组合", "加载率", "节能", "分配", "怎么开", "如何开", "运行方案"]),
    ("forecast_query", ["预测", "未来", "负荷", "用量"]),
    ("health_query", ["健康", "诊断", "振动", "温度", "劣化", "异常"]),
    ("status_overview", ["总览", "概况", "现状", "运行情况", "整体情况", "情况如何"]),
]

DEVICE_RE = re.compile(r"([45])\s*[#号]")
HORIZON_RE = re.compile(r"(15|30|60)\s*分钟")
FLOW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m3/min|立方米每分钟|方)")


def extract_device(text: str) -> str | None:
    m = DEVICE_RE.search(text)
    return f"{m.group(1)}#" if m else None


def extract_horizon(text: str) -> int | None:
    m = HORIZON_RE.search(text)
    return int(m.group(1)) if m else None


def extract_flow(text: str) -> float | None:
    m = FLOW_RE.search(text)
    return float(m.group(1)) if m else None


def classify(text: str) -> tuple[str, float]:
    t = (text or "").lower()
    scores: dict[str, int] = {}
    for intent, kws in _INTENT_KEYWORDS:
        score = sum(1 for kw in kws if kw in t)
        if score:
            scores[intent] = score
    if not scores:
        return "unknown", 0.0
    # 优先级: 关键词命中数优先, 其次按 _INTENT_KEYWORDS 顺序
    order = {name: i for i, (name, _) in enumerate(_INTENT_KEYWORDS)}
    intent = max(scores, key=lambda k: (scores[k], -order[k]))
    total = sum(scores.values())
    return intent, round(scores[intent] / max(total, 1), 2)
