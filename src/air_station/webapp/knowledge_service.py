"""本地知识库服务（供可视化网页的“知识中心 / Agent”调用）。

说明：本模块在仓库内提供一个**可运行的轻量知识检索服务**，用于演示前端知识中心
页面与 Agent 知识回答能力；知识条目来自项目自身的文档/配置/指标口径，不编造内容。
若由 C 侧提供正式知识库后端，前端仅需把 /api/knowledge/* 指向该后端即可。

检索：字符级 TF-IDF（sklearn），返回 {status, query, answer, confidence,
      kb_version, retrieval_mode, citations, elapsed_seconds}。
"""
from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

RETRIEVAL_MODE = "tfidf_char"
NO_ANSWER_TEXT = "知识库暂无可靠依据，请检查项目文档或补充知识来源。"
SCORE_THRESHOLD = 0.12

BUILT_AT = "2026-10-25 10:00:00"

# --------------------------------------------------------------------------- #
# 知识条目（内容取自项目自身文档与实现，source_path 为仓库相对路径）
# --------------------------------------------------------------------------- #
KB = [
    {
        "knowledge_id": "KB-DEVICE-001", "title": "机组档案与额定参数", "category": "设备档案",
        "section": "额定参数", "source_path": "README.md", "validation_mode": "真实数据",
        "content": "空压站含 4#、5# 两台离心式空压机。单机额定流量 268.9 m³/min，额定功率 1250 kW；"
                   "最低稳定流量工程假设为 120 m³/min；历史实测瞬时最大流量约 299.7 m³/min，仅作参考，"
                   "不作为持续安全容量。B 相电流恒为 0，不作为有效模型特征。",
    },
    {
        "knowledge_id": "KB-DEVICE-002", "title": "维护计数器哨兵值（65535）含义", "category": "设备档案",
        "section": "数据质量", "source_path": "README.md", "validation_mode": "真实数据",
        "content": "4# 维护计数器中的 65535 与约 4.29e9 按哨兵值（无效占位）处理，代表计数器溢出或未接入，"
                   "不是真实的剩余小时数，不能直接参与计算；此类值在标准化阶段被移除并标记。",
    },
    {
        "knowledge_id": "KB-DATA-001", "title": "数据范围与粒度", "category": "数据与指标",
        "section": "数据说明", "source_path": "README.md", "validation_mode": "真实数据",
        "content": "原始数据覆盖约 184 天，时间范围为 2026-03-12 至 2026-09-12，主要为 1 分钟粒度，"
                   "包括设备运行参数、设备状态监测、用气负荷、管网运行、环境参数、运行事件、维护保养等表。"
                   "标准化后产出 station_timeseries、device_timeseries、events、maintenance 等 Parquet，"
                   "Parquet 由 A 侧生成，其他成员只读。",
    },
    {
        "knowledge_id": "KB-DATA-002", "title": "指标口径与证据类型", "category": "数据与指标",
        "section": "证据分级", "source_path": "analysis/reports/acceptance/TEST_REPORT.md", "validation_mode": "项目演示规则",
        "content": "指标按证据强度分为三类：实测（真实数据可证）、仿真验证（模型或工程假设，不能代表现场）、"
                   "无法验证（缺少真实标签）。属于仿真或混合仿真的指标包括：综合能效提升率、用电成本降低率、"
                   "压力合格率与波动、加载率偏离度、大马拉小车时长；无法验证的包括：异常预警准确率/召回率/误报率、"
                   "异常预警提前量、喘振风险识别准确率。",
    },
    {
        "knowledge_id": "KB-DATA-003", "title": "混合仿真验证指标有哪些", "category": "数据与指标",
        "section": "验证方式", "source_path": "analysis/reports/acceptance/TEST_REPORT.md", "validation_mode": "混合仿真",
        "content": "混合仿真验证指一部分来自真实历史数据回放、一部分来自工程假设仿真的指标，"
                   "例如闭环压力动态、加载率与能效对比、喘振代理分级。这些结果必须在报告中标注验证方式，"
                   "不得把仿真结果表述为真实工厂现场效果。",
    },
    {
        "knowledge_id": "KB-FORECAST-001", "title": "负荷预测模型与输入输出", "category": "预测模型",
        "section": "输入输出", "source_path": "src/air_station/models/forecasting/service.py", "validation_mode": "离线回放",
        "content": "用气负荷预测采用 LightGBM，预测未来 15/30/60 分钟总用气流量。输入特征包括滞后项"
                   "（lag_0/1/5/15/30/60/1440）、滚动统计（roll_5/15/30/60 的均值/标准差/极值）、差分、"
                   "小时与星期周期编码、环境温湿度、运行台数等；输出为各时域的点预测值与 90% 预测区间"
                   "（predicted_flow、lower_bound、upper_bound），单位为 m³/min。",
    },
    {
        "knowledge_id": "KB-FORECAST-002", "title": "预测精度与评价口径", "category": "预测模型",
        "section": "评价口径", "source_path": "artifacts/metrics/forecast_metrics.json", "validation_mode": "离线回放",
        "content": "冻结测试段预测 MAPE 为 15 分钟 1.13%、30 分钟 1.57%、60 分钟 2.05%，满足 MAPE ≤ 8% 的项目目标。"
                   "MAPE 计算仅统计真实流量 ≥ 1 m³/min 的点，分母取 max(|y|,1)，避免停机零流量点抬高误差。"
                   "分钟级负荷近似随机游走，lag_1 是很强的简单基线，当前模型未全面优于 lag_1。",
    },
    {
        "knowledge_id": "KB-FORECAST-003", "title": "为什么没有优于 lag_1 基线", "category": "预测模型",
        "section": "局限", "source_path": "README.md", "validation_mode": "离线回放",
        "content": "分钟级用气负荷近似随机游走，lag_1（上一分钟实测值）本身就有极高精度，因此模型虽然达到 MAPE 目标，"
                   "但未全面超过 lag_1 基线。项目如实说明“达到预测精度目标”，不宣称“已全面优于所有简单基线”。",
    },
    {
        "knowledge_id": "KB-HEALTH-001", "title": "健康评分与风险等级", "category": "健康与维护",
        "section": "风险等级", "source_path": "analysis/reports/phaseA5/health_report.md", "validation_mode": "仿真",
        "content": "设备健康诊断采用无监督方法，输出 0–100 健康评分与四级风险：正常、关注、预警、严重。"
                   "评分由专家规则扣分与相对同负荷正常值的偏差共同决定，validation_mode=unsupervised。"
                   "风险等级用于提示与软约束，不能直接替代人工检修判断，也不自动触发真实停机。",
    },
    {
        "knowledge_id": "KB-HEALTH-002", "title": "健康风险为什么会影响调度", "category": "健康与维护",
        "section": "调度影响", "source_path": "configs/optimization.yaml", "validation_mode": "项目演示规则",
        "content": "调度目标函数中包含健康惩罚项（health_penalty_map）：正常 0.0、关注 0.2、预警 0.5、严重 1.0，"
                   "作为软惩罚参与机组组合选优，使高风险机组更不容易被选中或承担高负荷。当前健康模式为 observe，"
                   "仅记录建议，不按系数硬性降容，也不会自动停机；严重等级只给出建议停机提示，需人工确认。",
    },
    {
        "knowledge_id": "KB-HEALTH-003", "title": "故障码 26 的弱标签性质", "category": "健康与维护",
        "section": "数据局限", "source_path": "analysis/reports/acceptance/TEST_REPORT.md", "validation_mode": "仿真",
        "content": "故障码 26 仅为事件级弱标签，且两台设备事件高度同步，可能属于站级共因；当前弱标签分离度为负，"
                   "无法据此计算故障分类准确率/召回率/误报率。该项目如实保留这一局限，不声称已获得真实故障识别精度。",
    },
    {
        "knowledge_id": "KB-RUL-001", "title": "RUL 来源与适用边界", "category": "健康与维护",
        "section": "适用边界", "source_path": "analysis/reports/phaseA5_rul/rul_report.md", "validation_mode": "仿真",
        "content": "剩余寿命 RUL 由模拟退化数据训练的 GradientBoosting 模型迁移到真实遥测上估计，"
                   "validation_mode=simulation，仅用于健康惩罚/调度软约束演示，不代表真实设备剩余寿命，"
                   "过期或无效时应回退到健康等级。",
    },
    {
        "knowledge_id": "KB-DISPATCH-001", "title": "三种调度策略", "category": "调度优化",
        "section": "策略", "source_path": "analysis/reports/b/delivery/ALGORITHMS_AND_API.md", "validation_mode": "项目演示规则",
        "content": "统一调度接口 plan_dispatch 支持三种模式：b3 能效优先（复用 B3+B4 优化）、"
                   "balanced 低负荷优先（减少大马拉小车）、load_balanced 加载率均衡（机组间负荷均衡）。"
                   "三种模式都输出机组组合、逐机动作、预计供气与功率、喘振状态与安全回退标志。",
    },
    {
        "knowledge_id": "KB-DISPATCH-002", "title": "优化目标与约束", "category": "调度优化",
        "section": "目标函数", "source_path": "configs/optimization.yaml", "validation_mode": "项目演示规则",
        "content": "调度在四种机组组合（停机/仅 4#/仅 5#/双机）中枚举，目标为归一化后的加权最小："
                   "功率权重 1.0、启停权重 0.05、健康权重 0.10，可选寿命惩罚默认关闭。约束包括供气容量与备用、"
                   "压力区间、单机功率上限、最短开停时间与每小时切换次数上限。所有参数均外置在配置中。",
    },
    {
        "knowledge_id": "KB-DISPATCH-003", "title": "什么是大马拉小车工况", "category": "调度优化",
        "section": "定义", "source_path": "analysis/reports/b/delivery/B_DELIVERY_README.md", "validation_mode": "仿真",
        "content": "“大马拉小车”指机组处于运行状态但负荷率偏低（load_ratio = 流量/268.9 < 0.5）的工况，"
                   "即设备在明显低于其额定能力的区间运行、能耗效率偏低。该指标以“运行且负荷率<0.5 的机分钟”统计，"
                   "低负荷优先策略通过减少此类机分钟来降低能耗。",
    },
    {
        "knowledge_id": "KB-DISPATCH-004", "title": "执行权限与安全回退", "category": "调度优化",
        "section": "执行权限", "source_path": "analysis/reports/b/delivery/ALGORITHMS_AND_API.md", "validation_mode": "项目演示规则",
        "content": "所有调度输出 execution_permission=simulation_candidate_only，仅作为仿真候选，"
                   "不含任何 PLC 控制下发路径。当求解失败、超时或不可行时，系统统一安全回退到 B1 规则基线，"
                   "并标记 is_fallback=true，不伪装成优化成功。",
    },
    {
        "knowledge_id": "KB-SURGE-001", "title": "喘振风险等级与安全规则", "category": "喘振与安全",
        "section": "安全规则", "source_path": "src/air_station/safety/surge.py", "validation_mode": "仿真",
        "content": "喘振采用代理评分：以历史运行点低分位包络作为代理边界，计算计划流量相对边界的裕度，"
                   "分为低/中/高/未知。high 与 unknown 会触发 safety_blocked（需人工复核），不自动执行。"
                   "该边界是统计代理，不是厂家真实喘振线。",
    },
    {
        "knowledge_id": "KB-SURGE-002", "title": "为什么高喘振风险方案不能执行", "category": "喘振与安全",
        "section": "原因", "source_path": "configs/safety.yaml", "validation_mode": "仿真",
        "content": "喘振会导致机组剧烈振动甚至损坏，属于安全底线。当代理评估为高风险或证据不足（未知）时，"
                   "系统直接阻断该方案（safety_blocked=true），只允许人工复核；数据中存在大量未覆盖压力区间的未知项，"
                   "宁可拒答也不默认低风险，避免把不安全方案当作可执行方案。",
    },
    {
        "knowledge_id": "KB-OPS-001", "title": "可视化平台的启动方式", "category": "运维知识",
        "section": "运行", "source_path": "src/air_station/webapp/app.py", "validation_mode": "项目演示规则",
        "content": "可视化平台基于 FastAPI，根路径 / 直接返回首页，数据通过 /api/* 暴露。启动命令为 "
                   "python3 -m air_station.webapp.app（默认监听 0.0.0.0:8080，可用环境变量 PORT/HOST 覆盖），"
                   "服务内部会切换到仓库根目录以保证配置、模型与数据路径可解析。",
    },
    {
        "knowledge_id": "KB-API-001", "title": "预测与健康对外接口", "category": "系统接口",
        "section": "接口", "source_path": "analysis/reports/A_to_C_服务接口.md", "validation_mode": "项目演示规则",
        "content": "负荷预测对外入口为 forecast_json()，返回 status、model_type、prediction_base_time 与 "
                   "predictions（含 horizon_minutes、target_time、predicted_flow 与上下界）；"
                   "健康诊断对外入口为 health_json()，返回 status、validation_mode、evaluation_time 与 devices 列表。"
                   "两者均为纯函数，可被 MCP 或后端直接封装。",
    },
    {
        "knowledge_id": "KB-ACCEPT-001", "title": "赛题验收与证据分级", "category": "赛题验收",
        "section": "验收", "source_path": "analysis/reports/acceptance/TEST_REPORT.md", "validation_mode": "混合仿真",
        "content": "验收使用统一测试集（冻结测试段 2026-08-21 ~ 2026-09-12）与固定评价方法，覆盖预测、调度时延、"
                   "能效、成本、压力、加载率、启停、低负荷、异常预警、喘振识别、Agent 意图与接口可用性等指标。"
                   "不能由现有数据证明的指标明确标注为仿真验证或无法验证，禁止包装为现场效果。",
    },
    {
        "knowledge_id": "KB-FAQ-001", "title": "为什么停机点不参与预测 MAPE", "category": "常见问题",
        "section": "口径", "source_path": "configs/forecasting.yaml", "validation_mode": "离线回放",
        "content": "停机或低流量点的真实流量接近 0，会使 MAPE 分母极小、指标被异常放大。"
                   "因此口径规定 MAPE 仅统计真实流量 ≥ 1 m³/min 的点，分母取 max(|y|,1)，"
                   "保证评价方法稳定且与冻结指标一致。",
    },
    {
        "knowledge_id": "KB-FAQ-002", "title": "调度结果能直接下发到设备吗", "category": "常见问题",
        "section": "安全边界", "source_path": "README.md", "validation_mode": "项目演示规则",
        "content": "不能。项目属于竞赛原型：不直接连接真实 PLC 下发控制命令，不绕过设备保护逻辑，不自动执行真实停机，"
                   "所有控制能力先通过模拟接口验证；真实工业部署前必须经过设备厂商、工艺、安全和运维人员审核。",
    },
]

CATEGORIES = ["设备档案", "数据与指标", "预测模型", "健康与维护", "调度优化",
              "喘振与安全", "运维知识", "系统接口", "赛题验收", "常见问题"]

RECOMMENDED = [
    "维护计数器出现 65535 是什么意思？",
    "负荷预测模型的输入输出是什么？",
    "健康风险为什么会影响调度？",
    "什么是“大马拉小车”工况？",
    "为什么高喘振风险方案不能执行？",
    "哪些指标属于混合仿真验证？",
]


def _build_index():
    from sklearn.feature_extraction.text import TfidfVectorizer
    corpus = [f"{d['title']} {d['category']} {d['section']} {d['content']}" for d in KB]
    vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 2), min_df=1)
    mat = vec.fit_transform(corpus)
    return vec, mat


_VEC, _MAT = _build_index()


def status() -> dict:
    docs = len({d["source_path"] for d in KB})
    return {
        "status": "ready",
        "kb_version": "2026.10.25.1",
        "document_count": docs,
        "chunk_count": len(KB),
        "built_at": BUILT_AT,
        "stale": False,
        "retrieval_mode": RETRIEVAL_MODE,
        "message": "",
    }


def categories() -> dict:
    return {"categories": CATEGORIES, "recommended": RECOMMENDED}


def ask(question: str, top_k: int = 5) -> dict:
    import re
    from sklearn.metrics.pairwise import cosine_similarity
    t0 = time.perf_counter()
    q = (question or "").strip()
    if not q:
        return {"status": "no_answer", "query": q, "answer": NO_ANSWER_TEXT, "confidence": 0.0,
                "kb_version": status()["kb_version"], "retrieval_mode": RETRIEVAL_MODE,
                "citations": [], "elapsed_seconds": 0.0}
    # 厂商内部/未知故障码：知识库无依据，明确拒答，不编造含义
    m = re.search(r"故障码\s*([0-9]{1,6})", q)
    if m and int(m.group(1)) not in {26, 13, 6, 3, 1}:
        return {"status": "no_answer", "query": q, "answer": NO_ANSWER_TEXT, "confidence": 0.0,
                "kb_version": status()["kb_version"], "retrieval_mode": RETRIEVAL_MODE,
                "citations": [], "elapsed_seconds": round(time.perf_counter() - t0, 4)}
    qv = _VEC.transform([q])
    sims = cosine_similarity(qv, _MAT)[0]
    order = sims.argsort()[::-1]
    top_k = max(1, min(int(top_k or 5), len(KB)))
    citations, best = [], 0.0
    for i in order[:top_k]:
        s = float(sims[i])
        if s <= 0:
            continue
        best = max(best, s)
        d = KB[i]
        citations.append({
            "knowledge_id": d["knowledge_id"], "title": d["title"], "category": d["category"],
            "source_path": d["source_path"], "section": d["section"],
            "score": round(s, 4), "validation_mode": d["validation_mode"],
        })
    elapsed = round(time.perf_counter() - t0, 4)
    if not citations or best < SCORE_THRESHOLD:
        return {"status": "no_answer", "query": q, "answer": NO_ANSWER_TEXT, "confidence": 0.0,
                "kb_version": status()["kb_version"], "retrieval_mode": RETRIEVAL_MODE,
                "citations": [], "elapsed_seconds": elapsed}
    top = KB[order[0]]
    return {
        "status": "success", "query": q, "answer": top["content"],
        "confidence": round(min(best * 1.6, 0.99), 2),
        "kb_version": status()["kb_version"], "retrieval_mode": RETRIEVAL_MODE,
        "citations": citations, "elapsed_seconds": elapsed,
        "validation_mode": top["validation_mode"],
        "answer_title": top["title"], "answer_category": top["category"],
    }
