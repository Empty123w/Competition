"""Agent 工作流 (阶段 C): 意图理解 -> 工具链编排 -> 方案与可解释链路。

闭环: 自然语言需求 -> 调用 MCP 工具(预测/健康/喘振/调度/控制) -> 生成方案 ->
(可选)下发至模拟 PLC 执行并回收反馈 -> 记录会话, 支持多轮对话。
默认使用规则意图引擎; 配置 LLM 后自动启用并可回退。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from air_station.agent import intent as intent_mod
from air_station.agent import llm
from air_station.db import repository

DEFAULT_CONFIG = "configs/agent.yaml"

RISK_RANK = {"正常": 0, "关注": 1, "预警": 2, "严重": 3}

# 组合请求的知识检索提示(意图 -> 关键词)
_KB_HINT = {
    "dispatch_optimize": "健康风险如何影响调度 安全边界 备用",
    "control_execute": "健康风险如何影响调度 安全边界",
    "health_query": "健康分数计算 风险等级 无监督",
    "forecast_query": "负荷预测模型输入输出 验证方式",
    "rul_query": "RUL 模拟验证 软约束",
    "surge_query": "喘振风险 安全边界 高风险",
}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[agent] 配置文件缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


@dataclass
class Context:
    device: str | None = None
    horizon: int | None = None
    flow: float | None = None
    plan: dict | None = None
    question: str | None = None
    history: list[str] = field(default_factory=list)


def _classify(text: str, cfg: dict) -> tuple[str, float, str]:
    engine = cfg.get("intent", {}).get("engine", "auto")
    if engine in ("auto", "llm") and llm.is_enabled(cfg):
        got = llm.classify(text, cfg)
        if got is not None:
            return got[0], got[1], "llm"
    it, conf = intent_mod.classify(text)
    return it, conf, "rule"


def _run_tool(name: str, ctx: Context, results: dict, cfg: dict,
              persist: bool) -> None:
    from air_station.mcp_servers import tools
    try:
        if name == "forecast_load":
            results[name] = tools.forecast_load()
        elif name == "health_diagnose":
            results[name] = tools.health_diagnose()
        elif name == "rul_estimation":
            results[name] = tools.rul_estimation()
        elif name == "surge_warning":
            results[name] = tools.surge_warning()
        elif name == "dispatch_optimize":
            results[name] = tools.dispatch_optimize(
                demand_flow_m3_min=ctx.flow,
                horizon_minutes=ctx.horizon or 15)
            ctx.plan = results[name]
        elif name == "knowledge_search":
            from air_station.mcp_servers import tools
            results["knowledge_search"] = tools.knowledge_search(question=ctx.question or "")
        elif name == "scada_query":
            results[name] = tools.control_command("scada_query", persist=persist)
        elif name == "control_command":
            plan = ctx.plan or results.get("dispatch_optimize")
            if not plan:
                raise ValueError("[agent] 缺少调度方案, 无法下发控制指令")
            results[name] = tools.control_command("execute_plan", plan=plan,
                                                   persist=persist)
        elif name == "create_work_order":
            health = results.get("health_diagnose", {})
            dev = ctx.device or _worst_device(health)
            results[name] = tools.create_work_order(device_id=dev, persist=persist)
        else:
            raise ValueError(f"[agent] 未知工具: {name}")
    except Exception as e:  # noqa: BLE001
        results[name] = {"status": "error", "error": str(e)}


def _worst_device(health: dict) -> str:
    devices = health.get("devices", [])
    if not devices:
        return "4#"
    return max(devices, key=lambda d: RISK_RANK.get(d.get("risk_level", "正常"), 0))["device_id"]


def _compose(intent: str, results: dict, ctx: Context, execute: bool) -> tuple[str, list[str], list[str]]:
    explain: list[str] = []
    if intent == "knowledge_query":
        ks = results.get("knowledge_search", {})
        if ks.get("status") == "no_answer":
            explain.append("调用 knowledge_search → 无可靠依据, 明确拒答")
            return ks.get("answer", "知识库暂无可靠依据。"), explain, []
        cites = "; ".join(f"{c.get('knowledge_id')}({c.get('section')})"
                          for c in ks.get("citations", [])[:3])
        explain.append("调用 knowledge_search → 检索知识卡")
        if cites:
            explain.append("引用: " + cites)
        return ks.get("answer", ""), explain, []
    follow_up: list[str] = []
    if intent == "forecast_query" and "forecast_load" in results:
        fc = results["forecast_load"]
        ps = fc.get("predictions", [])
        seg = ", ".join(f"{p['horizon_minutes']}分钟 {p['predicted_flow']:.1f}"
                        f"(区间 {p['lower_bound']:.1f}~{p['upper_bound']:.1f})" for p in ps)
        explain.append("调用 forecast_load: LightGBM 负荷预测模型")
        return f"未来用气负荷预测(m3/min): {seg}。", explain, ["需要我据此生成调度方案吗?"]

    if intent == "health_query" and "health_diagnose" in results:
        hs = results["health_diagnose"]
        parts = [f"{d['device_id']} 健康分 {d['health_score']}({d['risk_level']}), "
                 f"主因: {d['top_anomaly_factor']}" for d in hs.get("devices", [])]
        explain.append("调用 health_diagnose: 无监督健康评分")
        msg = "设备健康诊断: " + "; ".join(parts) + "。"
        rul = results.get("rul_estimation", {})
        if rul.get("devices"):
            rul_seg = ", ".join(f"{d['device_id']} 约 {d['estimated_rul_hours']:.0f}h"
                                f"(区间 {d['lower_bound_hours']:.0f}~{d['upper_bound_hours']:.0f}h)"
                                for d in rul["devices"])
            msg += f" 剩余寿命(模拟): {rul_seg}。"
            explain.append("调用 rul_estimation: 模拟退化 RUL 模型")
        return msg, explain, ["需要我为异常设备生成工单吗?"]

    if intent == "rul_query" and "rul_estimation" in results:
        rul = results["rul_estimation"]
        if rul.get("status") == "error":
            return f"RUL 估计失败: {rul.get('error')}", explain, []
        seg = ", ".join(f"{d['device_id']} 约 {d['estimated_rul_hours']:.0f}h"
                        f"(区间 {d['lower_bound_hours']:.0f}~{d['upper_bound_hours']:.0f}h)"
                        for d in rul.get("devices", []))
        explain.append("调用 rul_estimation: 模拟退化 RUL 模型(validation_mode=simulation)")
        return (f"设备剩余寿命估计(模拟, 仅供调度演示): {seg}。", explain,
                ["需要我据此调整调度优先级吗?"])

    if intent == "surge_query" and "surge_warning" in results:
        sw = results["surge_warning"]
        parts = [f"{d.get('device_id')} 风险 {d.get('risk_level')}"
                 f"(裕度 {d.get('risk_margin')})" for d in sw.get("devices", [])]
        explain.append("调用 surge_warning → B assess_surge_risk(代理边界, 非真实喘振线)")
        blocked = " [安全阻断]" if sw.get("safety_blocked") else ""
        return (f"站级喘振风险 {sw.get('station_risk_level', 'unknown')}{blocked}; "
                + "; ".join(parts) + "。", explain, ["需要我调整调度以远离喘振区吗?"])

    if intent in ("dispatch_optimize", "control_execute") and "dispatch_optimize" in results:
        plan = results["dispatch_optimize"]
        if not isinstance(plan, dict) or plan.get("status") == "error":
            return f"调度优化失败: {plan.get('error')}", explain, []
        combo = plan.get("target_combination", [])
        actions = [a for a in plan.get("device_actions", [])
                   if a.get("action") in ("start", "adjust") and a.get("running_after")]
        seg = ", ".join(f"{a['device_id']} {a.get('target_flow_m3_min')} m3/min"
                        for a in actions) or "无"
        msg = (f"调度模式 {plan.get('mode', 'b3')}: 需求 {plan.get('required_flow_m3_min')} m3/min, "
               f"建议组合 {combo}, 分配 {seg}; 预计机群功率 {plan.get('estimated_power_kw')} kW, "
               f"喘振 {plan.get('surge_status')}")
        if plan.get("is_fallback"):
            msg += " [B 失败回退: 需人工复核]"
        if plan.get("safety_blocked"):
            msg += " [B 安全阻断: 禁止执行]"
        msg += "。"
        explain.append(f"调用 dispatch_optimize → B plan_dispatch(mode={plan.get('mode', 'b3')})")
        if plan.get("reason_codes"):
            explain.append("B 原因码: " + ", ".join(str(c) for c in plan["reason_codes"]))
        cmeta = plan.get("c_meta", {})
        if cmeta:
            explain.append(f"仿真候选={cmeta.get('candidate_usable')}, "
                           f"需人工复核={cmeta.get('needs_human_review')}, "
                           f"执行许可={cmeta.get('execution_allowed')}")

        if intent == "control_execute" and execute:
            ctl = results.get("control_command", {})
            if ctl and ctl.get("status") != "error":
                if ctl.get("accepted"):
                    ok = sum(1 for r in ctl.get("results", []) if r.get("accepted"))
                    msg += (f" 已向模拟 PLC 下发 {ctl.get('command_count', 0)} 条指令, "
                            f"接受 {ok} 条, 实测机群功率 {ctl.get('actual_total_power_kw', 0):.0f} kW。")
                    explain.append("调用 control_command(execute_plan): 下发->模拟执行->反馈闭环")
                else:
                    msg += f" 执行被准入闸门拒绝: {ctl.get('rejected_reason')}。"
                    explain.append("control 准入闸门拒绝了该方案(需人工复核)")
            elif ctl.get("status") == "error":
                msg += f" 控制下发失败: {ctl.get('error')}。"
        follow_up = ["需要查看执行反馈吗?"] if execute else ["需要执行该方案吗?"]
        return msg, explain, follow_up

    if intent == "status_overview":
        segs = []
        if "scada_query" in results and results["scada_query"].get("status") != "error":
            sc = results["scada_query"]
            running = [d["device_id"] for d in sc.get("devices", []) if d.get("running")]
            segs.append(f"在运机组 {running or '无'}")
            explain.append("调用 control_command(scada_query): 模拟 SCADA 快照")
        if "health_diagnose" in results and results["health_diagnose"].get("status") != "error":
            hs = results["health_diagnose"]
            segs.append("健康: " + ", ".join(f"{d['device_id']} {d['risk_level']}"
                                            for d in hs.get("devices", [])))
            explain.append("调用 health_diagnose")
        if "rul_estimation" in results and results["rul_estimation"].get("status") != "error":
            rul = results["rul_estimation"]
            segs.append("剩余寿命(模拟): " + ", ".join(
                f"{d['device_id']} {d['estimated_rul_hours']:.0f}h"
                for d in rul.get("devices", [])))
            explain.append("调用 rul_estimation")
        if "surge_warning" in results and results["surge_warning"].get("status") != "error":
            segs.append(f"喘振风险 {results['surge_warning'].get('station_risk_level', 'unknown')}")
            explain.append("调用 surge_warning")
        return "运行总览: " + "; ".join(segs) + "。", explain, ["需要我优化当前调度吗?"]

    if intent == "workorder" and "create_work_order" in results:
        wo = results["create_work_order"]
        if wo.get("status") == "error":
            return f"工单生成失败: {wo.get('error')}", explain, []
        explain.append("调用 create_work_order: 模拟 EAM 工单")
        wo_id = wo.get("work_order_id") or "(未落库)"
        return (f"已生成工单 {wo_id}, 设备 {wo.get('device_id')}, "
                f"内容: {wo.get('reason')}, 优先级 {wo.get('priority')}。", explain, [])

    explain.append("未匹配到明确意图, 返回运行总览")
    return ("我暂时无法确定你的具体需求。你可以让我: 预测用气负荷、诊断设备健康、"
            "评估喘振风险、优化调度, 或下发控制指令。", explain,
            ["试试点: 优化当前调度方案"])


def run_agent(user_input: str, execute: bool = False, persist: bool = True,
              session_id: str | None = None, config_path: str = DEFAULT_CONFIG) -> dict:
    t0 = time.perf_counter()
    cfg = load_config(config_path)
    ctx = Context()
    if session_id and persist:
        prev = repository.get_agent_session(session_id)
        if prev:
            pctx = prev.get("response", {}).get("context", {})
            ctx.plan = pctx.get("plan")
            ctx.device = pctx.get("device")
            ctx.horizon = pctx.get("horizon")
            ctx.flow = pctx.get("flow")

    ctx.question = user_input
    ctx.device = intent_mod.extract_device(user_input) or ctx.device
    ctx.horizon = intent_mod.extract_horizon(user_input) or ctx.horizon
    ctx.flow = intent_mod.extract_flow(user_input) or ctx.flow

    intent, conf, engine = _classify(user_input, cfg)
    # 若指令含"执行/下发", 升级为 control_execute
    if "control_execute" != intent and any(k in user_input for k in ("执行", "下发", "启停")):
        intent = "control_execute"
        conf = max(conf, 0.6)

    # 知识库路由(增量): 未知问题先走知识库; 解释型问题走知识库;
    # 组合请求(实时动作+解释) 动态工具后追加知识解释。
    _kb = intent_mod.is_knowledge_question(user_input)
    _rt = intent_mod.has_realtime_action(user_input)
    if _kb and not _rt:
        intent, conf = "knowledge_query", max(conf, 0.5)
    pipeline = list(cfg.get("pipelines", {}).get(intent, ["knowledge_search"]))
    if _kb and _rt and intent != "knowledge_query" and "knowledge_search" not in pipeline:
        pipeline.append("knowledge_search")
    results: dict = {}
    for name in pipeline:
        # control_execute 仅在显式 execute=True 时真正下发控制指令
        if name == "control_command" and not execute:
            continue
        _run_tool(name, ctx, results, cfg, persist)

    # 组合请求(实时动作 + 解释): 用意图提示补足知识检索
    if _kb and _rt and intent != "knowledge_query":
        from air_station.mcp_servers import tools
        hint = _KB_HINT.get(intent, "")
        results["knowledge_search"] = tools.knowledge_search((user_input + " " + hint).strip())
    answer, explain_chain, follow_up = _compose(intent, results, ctx, execute)
    # 组合请求: 追加知识解释(不改动态结果)
    if intent != "knowledge_query" and results.get("knowledge_search", {}).get("status") == "success":
        ks = results["knowledge_search"]
        if ks.get("answer"):
            answer = answer + " 补充解释: " + ks["answer"]
            explain_chain.append("调用 knowledge_search → 知识解释")

    tools_used = [n for n in pipeline if n in results and results[n].get("status") != "error"]
    elapsed = round(time.perf_counter() - t0, 3)
    response = {
        "status": "success",
        "session_id": session_id,
        "intent": intent,
        "intent_confidence": conf,
        "engine": engine,
        "answer": answer,
        "tools_used": tools_used,
        "explain_chain": explain_chain,
        "follow_up": follow_up,
        "plan": results.get("dispatch_optimize") if results.get("dispatch_optimize", {}).get("status") != "error" else None,
        "tool_results": results,
        "elapsed_seconds": elapsed,
        "context": {"plan": ctx.plan, "device": ctx.device,
                    "horizon": ctx.horizon, "flow": ctx.flow},
    }
    if persist:
        try:
            sid = repository.save_agent_session(
                user_input, intent, answer, tools_used,
                {"context": response["context"]})
            response["session_id"] = sid
        except Exception:  # noqa: BLE001
            pass
    return response


def cli() -> None:
    """控制台入口: air-station-agent "自然语言需求"."""
    import json
    import sys
    text = " ".join(sys.argv[1:]) or "3号机组振动偏高，请优化调度"
    execute = "--execute" in sys.argv
    text = text.replace("--execute", "").strip()
    print(json.dumps(run_agent(text, execute=execute, persist=True),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
