"""可选 LLM 客户端 (OpenAI 兼容), 不可用时自动回退规则引擎 (阶段 C)。"""
from __future__ import annotations

import json
import os


def resolve_config(cfg: dict) -> dict:
    llm = (cfg or {}).get("llm", {})
    return {
        "base_url": os.environ.get("AIRSTATION_LLM_BASE_URL", llm.get("base_url", "") or ""),
        "api_key": os.environ.get("AIRSTATION_LLM_API_KEY", llm.get("api_key", "") or ""),
        "model": os.environ.get("AIRSTATION_LLM_MODEL", llm.get("model", "gpt-4o-mini")),
        "temperature": float(llm.get("temperature", 0.2)),
        "timeout_s": float(llm.get("timeout_s", 20)),
    }


def is_enabled(cfg: dict) -> bool:
    c = resolve_config(cfg)
    engine = (cfg or {}).get("intent", {}).get("engine", "auto")
    if engine == "rule":
        return False
    return bool(c["base_url"] and c["api_key"])


def chat(messages: list[dict], cfg: dict) -> str | None:
    """调用 OpenAI 兼容接口; 任何失败返回 None 以便回退。"""
    c = resolve_config(cfg)
    if not (c["base_url"] and c["api_key"]):
        return None
    try:
        import httpx
        url = c["base_url"].rstrip("/") + "/chat/completions"
        resp = httpx.post(url, timeout=c["timeout_s"],
                          headers={"Authorization": f"Bearer {c['api_key']}",
                                   "Content-Type": "application/json"},
                          json={"model": c["model"], "temperature": c["temperature"],
                                "messages": messages})
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return None


def classify(text: str, cfg: dict) -> tuple[str, float] | None:
    """用 LLM 做意图分类, JSON 输出; 失败返回 None。"""
    intents = ["forecast_query", "health_query", "surge_query", "dispatch_optimize",
               "control_execute", "workorder", "status_overview", "unknown"]
    sys = ("你是空压站调度助手的意图分类器。只返回 JSON: "
           '{"intent": "<one of %s>", "confidence": <0-1>}。' % ", ".join(intents))
    content = chat([{"role": "system", "content": sys},
                    {"role": "user", "content": text}], cfg)
    if not content:
        return None
    try:
        start, end = content.find("{"), content.rfind("}")
        obj = json.loads(content[start:end + 1])
        intent = obj.get("intent", "unknown")
        if intent not in intents:
            return None
        return intent, float(obj.get("confidence", 0.5))
    except Exception:  # noqa: BLE001
        return None
