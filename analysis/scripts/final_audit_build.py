"""全项目指标审计构建器 (只读汇总; 不改算法/接口)。

读取工作区 B 组证据 JSON + 已审计的 A/C/D 数值 (团队 main 08915e4), 生成:
  analysis/reports/final_audit/
    FINAL_COMPETITION_KPI.md
    ALL_ALGORITHMS_COMPARISON.md
    DATA_AND_METRIC_DEFINITIONS.md
    FAILED_AND_MISSING_METRICS.md
    FINAL_VERSION_AND_DELIVERY_CHECK.md
    kpi_ledger.csv / kpi_ledger.json
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

WS = Path(".").resolve()
B = WS / "analysis/reports/b"
FIN = B / "final"
ST2 = B / "stage2"
ST3 = B / "stage3"
OUT = WS / "analysis/reports/final_audit"
TEAM_SHA = "08915e4 (Wxyun113/Competition main)"
B_SHA = "e2c93f9 (feat/b-stage2-metric-optimization)"
DATA_NOTE = ("需求=真实 SCADA 站级总供气量 (历史代理需求, 非独立实测用户侧需求); "
             "能效=真实历史输入驱动的反事实模型回测(B2 功率模型估计); "
             "压力=真实历史输入驱动动态闭环(工程压力模型); 喘振=1Hz 人工合成事件仿真")


def L(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rows_from_b():
    ted = L(FIN / "test_energy_diagnosis.json")
    lr = L(FIN / "load_ratio.json")
    cd = L(FIN / "contribution_dispatch.json")
    robust = L(FIN / "loadratio_robustness.json")
    surge = L(ST3 / "surge/surge_optimized.json")
    s2 = L(ST2 / "surge/surge_eval.json")
    pres = L(FIN / "contribution_pressure.json")
    preal = {w: L(FIN / f"pressure_real_{w}.json") for w in ("dev", "valid", "test")}
    pdev = L(FIN / "pressure_dev_analysis.json")
    ideal = ted["ideal_lower_bound"] if ted else {}
    return ted, lr, cd, robust, surge, s2, pres, preal, pdev, ideal


def md_kpi():
    ted, lr, cd, robust, surge, s2, pres, preal, pdev, ideal = rows_from_b()
    P = ted["pooled"]
    j = P["joint_nompc"]; jd = P["_deviation"]["joint_nompc"]
    lrj = lr["pooled"]["joint"] if lr else {}
    b3 = P["b3_b4"]
    lines = [
        "# 空压站多机协同智能调度 Agent —— 全项目最终指标总表",
        "",
        f"> 审计基准: 团队 main `{TEAM_SHA}`; B 组实验分支 `{B_SHA}`。",
        f"> 数据说明: {DATA_NOTE}。",
        "> 判定: 已达标 / 未达标 / 部分达标 / 已实现但未测试 / 缺少必要真实数据 / 未实现 / 无法核查。",
        "",
        "## 0. 评价范围 (样本口径) 核对 —— 不得混写",
        "",
        "| 口径 | 策略集合 | 运行步(service) | joint_nompc dev/valid/test/pooled SEC降% |",
        "|---|---|---|---|",
        "| **主口径** | b1,b3,bal,lrb_t10,joint_nompc,joint | **10793** | 10.49 / 3.84 / 2.98 / **7.842** |",
        "| 宽口径 | b1,joint_nompc | 11048 | 10.203 / 3.843 / 2.955 / 7.692 |",
        "| 窄口径(含b3_lrb) | +b3_lrb (8策略) | 10789 | — / — / — / 7.833 |",
        "",
        "> 差异来源: 纳入更多被比较策略后, 需剔除其不可行步 -> 样本 10793/11048/10789。",
        "> 本报告主表统一采用**主口径 10793**; 稳健性对照另行标注 11048; 贡献消融标注 10789。",
        "",
        "## A. 赛题 15 项 KPI (官方目标) —— 全项目现状",
        "",
        "| 组 | 指标 | 目标 | 实际 | 算法/模块 | 窗口/场景 | 样本 | 公式 | 数据来源 | 证据等级 | 达标 | 未达原因 | 证据文件 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    # 行: 15 项
    def R(g, name, tgt, val, mod, scope, n, formula, src, lvl, ok, why, ev):
        lines.append(f"| {g} | {name} | {tgt} | {val} | {mod} | {scope} | {n} | {formula} | {src} | {lvl} | {ok} | {why} | {ev} |")
    R("B", "系统综合能效提升率", "≥6%", f"pooled {j['sec_reduction_pct']}% (dev {ted['per_window']['dev']['joint_nompc']['sec_reduction_pct']}/valid {ted['per_window']['valid']['joint_nompc']['sec_reduction_pct']}/test {ted['per_window']['test']['joint_nompc']['sec_reduction_pct']})", "joint_nompc (B3+均衡)",
      "dev/valid/test/pooled", f"{P['_steps']} 步", "SEC=ΣE/ΣV; 降幅 vs B1", "真实历史反事实回测", "②真实驱动模型回测", "部分达标",
      "valid/test 单窗口<6%; test 受高负荷+B1已高效+10pp 约束限制", "final/TEST_ENERGY_DIAGNOSIS.md")
    R("B", "加载率偏离度", "≤±10%", f"pooled joint_nompc 均 {jd['mean_pp']}pp / P95 {jd['p95_pp']}pp / 最大 {jd['max_pp']}pp; 双机达标率 {(lrj.get('pass_rate_both') or 0)*100:.1f}%",
      "joint_nompc", "pooled(双机步)", f"{lrj.get('n_both')} 双机步", "D=|λ4−λ5|(pp), λ=Q/268.9", "真实历史反事实回测", "②真实驱动模型回测",
      "部分达标(内部口径)", "官方无公式; 内部解释非官方确认", "final/LOAD_RATIO_REPORT.md")
    R("B", "供气压力合格率", "≥99.5%", "valid 0.9998 / test 0.9957 / dev 0.8592", "joint(含MPC)", "dev/valid/test 全窗口",
      "各窗 ~5000-40000 步", "p∈[0.55,0.66] 步占比", "真实压力基线+真实需求闭环", "③真实驱动闭环仿真", "部分达标",
      "dev 13.1% 步需求>容量(物理), 合格率受限", "final/PRESSURE_DEV_ANALYSIS.md")
    R("B", "喘振预警准确率", "≥90%", f"{surge['after_stabilized']['test']['time_level']['accuracy']}", "GRU优化(+B4)", "1Hz 合成 test", "35 事件",
      "(TP+TN)/N 逐秒; 同时披露 F1/FNR", "人工合成高频仿真", "④人工合成仿真", "已达标(仿真)",
      "非真实喘振标签, 代理边界", "stage3/surge/surge_optimized.json")
    R("A", "非计划停机次数下降率", "≥50%", "92.08% (仿真)", "A7 维护策略", "混合仿真 480 episodes", "240 故障/240 正常",
      "(base-proposed)/base", "真实遥测基底+注入退化标签", "④人工合成仿真", "部分达标",
      "无真实停机记录; 注入标签仿真", "artifacts/metrics/maintenance_metrics.json")
    R("B", "调度方案生成时间", "≤60s", "b3 p50 35ms / p95 49ms / max 97ms", "B3/joint_nompc", "valid 抽测", "200",
      "单次 plan_dispatch 端到端 (算法侧)", "真实回测实测", "⑤软件测试", "已达标", "", "analysis/reports/b/kpi_latency.json")
    R("C", "自然语言指令理解准确率", "≥90%", "未给出系统化准确率 (仅单元样例)", "C agent/intent.py", "—", "无标注测试集",
      "分类正确率", "—", "⑦尚无有效验证证据", "已实现但未测试", "缺带标注的 NLU 测试集", "src/air_station/agent/intent.py")
    # 加分
    R("B", "单站年用电成本降低率", "5–10%", f"≈{j['energy_saving_equalV_pct']}% (服务等价节电率年化)", "joint_nompc", "pooled", f"{P['_steps']} 步",
      "节电率=(E_b1−E_x)/E_b1; 年化×电价0.7元/kWh(假设)", "真实历史回测+成本假设", "⑥假设估算", "部分达标",
      "非直接等于 SEC 降幅; 电价/年化为假设", "final/ENERGY_VERIFY.md")
    R("A", "MTBF 提升率", "≥30%", "1163% (仿真)", "A7", "混合仿真", "240+240",
      "(mtbf_p−mtbf_b)/mtbf_b", "真实遥测+注入标签", "④人工合成仿真", "部分达标", "仿真值不可当现场", "artifacts/metrics/maintenance_metrics.json")
    R("B", "大马拉小车工况时长下降率", "≥60%", f"pooled {j['low_reduction_pct']}% (bal 76.0%)", "joint_nompc/bal", "pooled", f"{P['_steps']} 步",
      "低负荷机分钟(λ<0.5)降幅 vs B1", "真实历史反事实回测", "②真实驱动模型回测", "已达标", "", "final/b_final_kpi.json")
    R("B", "压力波动幅度下降率", "≥30%", "dev 45.1 / valid 47.6 / test 92.6%", "joint(MPC)", "各窗口", "同上",
      "(std_b1−std_joint)/std_b1", "真实需求驱动闭环", "③真实驱动闭环仿真", "已达标(仿真)", "", "final/pressure_real_*.json")
    R("A", "异常预警提前时间", "≥24h", "中位 30h (仿真)", "A7 趋势预警", "混合仿真", "240",
      "预警时刻→故障时刻", "真实遥测+注入标签", "④人工合成仿真", "部分达标", "无真实故障标签", "artifacts/metrics/maintenance_metrics.json")
    R("B/C", "调度策略自适应迭代周期", "≤7天", "ARD 在线残差修正有; 7天闭环未建立", "B adaptive_robust + C workflow", "—", "—",
      "策略更新周期", "—", "⑦尚无有效验证证据", "部分达标/未实现", "无在线迭代闭环与计时证据", "src/air_station/optimization/adaptive_robust.py")
    R("B", "喘振预警提前时间", "≥30s", "提前≥30s 覆盖 0.914 (有前兆 0.97)", "GRU优化", "1Hz 合成 test", "35 事件",
      "事件起点−首次告警; 覆盖=占比", "人工合成高频仿真", "④人工合成仿真", "已达标(仿真)",
      "rapid 无前兆事件 0 提前", "stage3/surge/surge_optimized.json")
    R("C", "关键决策可解释性评分", "≥80分", "决策链已实现; 无评分标尺/前端展示", "C agent/workflow explain_chain", "—", "—",
      "—(无官方评分表)", "—", "⑦尚无有效验证证据", "已实现但未测试", "无评分方法; D 前端缺", "src/air_station/agent/workflow.py")
    lines += ["", "## B. 功能/性能与验收项 (任务书 表格 1-3 + 正文)", "",
              "| 组 | 要求 | 目标 | 现状 | 达标 | 证据/说明 |", "|---|---|---|---|---|---|"]
    func = [
        ("A", "用气负荷预测 MAPE", "≤8%", "test MAPE 15/30/60min = 1.13/1.57/2.05%", "已达标", "artifacts/metrics/forecast_metrics.json"),
        ("A", "预测计算时间", "≤10s", "≈1.4s", "已达标", "forecast 服务 elapsed"),
        ("A", "分钟级/小时级预测", "支持", "15/30/45/60min", "已达标", "forecast forecast_features"),
        ("A", "数据质量自动检测", "≥95%", "有 A1 校验; 准确率未量化", "已实现但未测试", "analysis/reports/phaseA1"),
        ("A", "工况状态识别", "≥90%", "负荷等级/运行状态已实现; 准确率未量化", "已实现但未测试", "A features"),
        ("A", "设备健康诊断 (健康指数+RUL)", "输出", "健康无监督 + RUL 仿真(MAE68.6/within20%=88.6%)", "部分达标", "health_metrics.json / rul_metrics.json"),
        ("B", "最优机组组合决策", "≤30s", "b3 max 97ms", "已达标", "kpi_latency.json"),
        ("B", "多目标平衡决策", "≤60s", "同调度耗时", "已达标", "同上"),
        ("B", "滚动调度周期", "5–15min", "回测步长 15min; 闭环 5min", "已达标", "backtest/harness"),
        ("B", "压力设定值优化", "波动降≥30%", "见压力波动 45-93%", "已达标(仿真)", "pressure_real_*.json"),
        ("B", "喘振风险分级 + 边界在线修正", "低/中/高 + 在线", "分级✅; 在线修正未见", "部分达标", "safety/surge.py"),
        ("C", "5 个必备 MCP 服务", "5", "10 个工具 (含必备5)", "已达标", "mcp_servers/tools.py"),
        ("C", "跨系统工具调用 (SCADA/PLC/EAM/工单)", "支持", "模拟实现", "已达标(模拟)", "control/"),
        ("C", "多轮对话", "≥10轮", "配置支持; 未验收", "已实现但未测试", "agent/workflow.py"),
        ("C", "主动告警推送", "≤5s", "未实现推送", "未实现", "—"),
        ("D", "可视化 (总览/对比/热力图/知识面板)", "必备", "无前端代码", "未实现", "团队 main 无 vue/tsx/html"),
        ("B/C", "控制指令生成/下发/反馈/工单时延", "≤5/3/10/30s", "模拟控制有延时字段; 未专项计测", "已实现但未测试", "control/ + 返回 elapsed"),
        ("A/C", "数据读取延迟", "≤3s", "本地 Parquet; 未专项计测", "已实现但未测试", "data/"),
        ("A/C", "设备状态刷新延迟", "≤5s", "API 实时读; 未计测", "已实现但未测试", "api/"),
        ("B", "异常工况应急响应", "≤10s", "安全联锁即时(模拟); 未专项计测", "已实现但未测试", "control/interlock.py"),
        ("B", "维保计划决策", "≤30s", "A7 维护逻辑存在; 未计测", "已实现但未测试", "phaseA7"),
        ("A/D", "设备特性曲线修正误差", "≤3%", "无真值曲线", "缺少必要真实数据", "—"),
        ("C", "系统可用率", "≥99.9%", "未测", "已实现但未测试", "—"),
        ("C", "容器化部署", "支持", "Dockerfile + compose (api+mcp)", "已达标", "deployment/"),
        ("B", "B01–B20 验收测试", "20 项", "20 passed (内部验收集)", "已达标", "tests/optimization/test_b01_b20_acceptance.py"),
        ("C", "知识库问答 (K)", "Top-3 命中", "Top-3 88.46%, 拒答100%, 引用100%, p95 0.066s", "已达标", "analysis/reports/phaseK/knowledge_acceptance.json"),
    ]
    for g, req, tgt, cur, ok, ev in func:
        lines.append(f"| {g} | {req} | {tgt} | {cur} | {ok} | {ev} |")
    (OUT / "FINAL_COMPETITION_KPI.md").write_text("\n".join(lines), encoding="utf-8")


def md_algorithms():
    ted, lr, cd, robust, surge, s2, pres, preal, pdev, ideal = rows_from_b()
    l = ["# 全部算法可比对照 (按职责分类; 不得跨类混表)", ""]
    # 调度
    l += ["## 1. 调度优化算法 (真实历史反事实回测; service 口径)", "",
          "| 策略 | dev SEC降% | valid SEC降% | test SEC降% | pooled SEC降% | pooled 同服务节电% | pooled 低负荷降% | pooled 偏离均pp | P95pp | 最大pp |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in ("b1_rule", "b3_b4", "bal", "lrb_t10", "joint_nompc", "joint"):
        p = ted["pooled"][s]; d = ted["pooled"]["_deviation"][s]
        l.append(f"| {s} | {ted['per_window']['dev'][s]['sec_reduction_pct']} | {ted['per_window']['valid'][s]['sec_reduction_pct']} | "
                 f"{ted['per_window']['test'][s]['sec_reduction_pct']} | {p['sec_reduction_pct']} | {p['energy_saving_equalV_pct']} | "
                 f"{p['low_reduction_pct']} | {d['mean_pp']} | {d['p95_pp']} | {d['max_pp']} |")
    l += ["", "另含 b3_lrb / mpc_hist (贡献实验): pooled SEC降% = "
          f"{cd['table']['b3_lrb']['sec_reduction_pct']} / {cd['table']['mpc_hist']['sec_reduction_pct']}; "
          f"b3_lrb 低负荷降 {cd['table']['b3_lrb']['low_reduction_pct']}%, mpc_hist D3 {cd['table']['mpc_hist']['d3_both']}。", ""]
    l += ["### 配对差异 (bootstrap 95% CI)", ""]
    for k, v in cd["stats"].items():
        if isinstance(v, dict):
            l.append(f"- {k}: SEC 差 {v['sec_diff']}, CI [{v['ci95_low']}, {v['ci95_high']}]")
    # 压力
    l += ["", "## 2. 压力控制算法 (真实需求驱动闭环; 前 2000 步)", "",
          "| 窗口 | 控制器 | 合格率 | 波动std | 需求满足度 | 未满足缺口m³ | 满载步占比 | 耗时ms/步 |",
          "|---|---|---|---|---|---|---|---|"]
    for w, o in pres["windows"].items():
        for k in ("reactive_pi", "mpc_simple", "mpc_ml", "b1", "joint_nompc", "joint"):
            m = o.get(k)
            if m:
                l.append(f"| {w} | {k} | {m['compliance']} | {m['std']} | {m['demand_satisfaction']} | {m['unmet_flow_m3']} | {m['cap_step_share']} | {m['decide_ms_per_step']} |")
    l += ["", "全窗口 (真实需求闭环):",
          "| 窗口 | 真实基线合格率 | b1 | joint | 波动降% | joint未满足缺口m³ | 储气中性 |", "|---|---|---|---|---|---|---|"]
    for w in ("dev", "valid", "test"):
        d = preal[w]; c = d["counterfactual"]
        l.append(f"| {w} | {d['real_baseline']['CAL05']['compliance']} | {c['b1']['compliance_CAL05']} | "
                 f"{c['joint']['compliance_CAL05']} | {c['joint'].get('std_reduction_vs_b1_pct')} | "
                 f"{c['joint']['unserved_deficit_m3']} | {c['joint']['storage_neutral']} |")
    # 喘振
    l += ["", "## 3. 喘振预警算法 (1Hz 人工合成事件, test 冻结; 真值二次边界 vs B4 线性代理, 机制独立)", "",
          "| 方案 | Acc | Prec | Recall | F1 | FNR | FPR | 事件召回 | 提前≥30s覆盖 | 误报次数 | 误报/h |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    if s2:
        for k in ("b4_ew_rule", "lightgbm", "gru", "lstm", "ensemble", "gru_and_b4"):
            v = s2["models"].get(k)
            if not v:
                continue
            tl, el = v["test"]["time_level"], v["test"]["event_level"]
            l.append(f"| {k} | {tl['accuracy']} | {tl['precision']} | {tl['recall']} | {tl['f1']} | {tl['fnr']} | {tl['fpr']} | "
                     f"{el['recall_event_level']} | {el['lead_ge30_coverage']} | {el['false_alarms']} | {el['false_alarms_per_hour']} |")
    if surge:
        tl, el = surge["after_stabilized"]["test"]["time_level"], surge["after_stabilized"]["test"]["event_level"]
        l.append(f"| **GRU优化**(校准+迟滞+冷却) | {tl['accuracy']} | {tl['precision']} | {tl['recall']} | {tl['f1']} | {tl['fnr']} | "
                 f"{tl['fpr']} | {el['recall_event_level']} | {el['lead_ge30_coverage']} | {el['false_alarms']} | {el['false_alarms_per_hour']} |")
    # 安全
    l += ["", "## 4. 安全阻断 (B4)", "",
          "- 高危/未知 -> `safety_blocked=true` -> 不执行 (joint/回测均 0 次被阻断却执行); 误操作计数 = 0。",
          "- 回测中 joint 被 B4 阻断(exec=False, status=success) 步数见 `backtest_*.json` fallback (joint 0)。", ""]
    # A
    l += ["## 5. A 组模型对照 (团队 main 证据)", "",
          "| 模块 | 模型 | 指标 | 值 | 窗口 | 证据 |", "|---|---|---|---|---|---|",
          "| 负荷预测 | LightGBM | MAPE 15/30/60min | 1.13/1.57/2.05% | test | forecast_metrics.json |",
          "| 负荷预测(滚动) | LightGBM | WAPE 15/30/60min | 1.71/2.10/2.69% | dev | forecast_rolling_metrics.json |",
          "| 负荷预测(滚动) | LightGBM | WAPE 15min | 1.72% | valid | 同上 |",
          "| 健康诊断 | 无监督 | 异常率 (4#/5#) | 7.42%/6.22% | 全量 | health_metrics.json |",
          "| RUL | GBR | MAE/MAPE/within20% | 68.6/9.17%/88.61% | test(仿真) | rul_metrics.json |",
          "| 维护预警 | 趋势+计划 | 预警准确率/提前中位 | 92.9%/30h | 混合仿真 | maintenance_metrics.json |",
          "| 压力模型(B2) | persistence | R² (valid/test) | 0.803/0.951 | valid/test | pressure_metrics.json |",
          "| 设备性能(B2) | 功率-流量 | MAE kW | dev 50/valid 43/test 31 | dev/valid/test | performance_metrics.json |", ""]
    # C
    l += ["## 6. C 组交互与集成 (团队 main)", "",
          "- MCP 工具 10 个 (含 5 必备): forecast_load/dispatch_optimize/health_diagnose/surge_warning/control_command + rul_estimation/create_work_order/maintenance_warning/knowledge_search/orchestrate。",
          "- 测试: `tests/agent/api/mcp/knowledge/control/db` → **52 passed** (本机安装 `mcp` 后; 之前 1 例因缺依赖失败)。",
          "- NLU: 规则意图引擎 (intent.py) + 可选 LLM 覆盖; **无系统化准确率测试集**。",
          "- 知识库 K: Top-3 88.46%, 拒答 100%, 引用 100%, 检索 p95 0.066s。",
          "- 容器化: `deployment/Dockerfile` + `docker-compose.yml` (api 8000 + mcp 8765)。", ""]
    (OUT / "ALL_ALGORITHMS_COMPARISON.md").write_text("\n".join(l), encoding="utf-8")


def md_definitions():
    ted, lr, cd, robust, surge, s2, pres, preal, pdev, ideal = rows_from_b()
    l = ["# 数据与指标定义 (审计口径)", "",
         "## 1. 数据集", "",
         "| 数据集 | 来源 | 时间范围 | 采样 | 用途 |", "|---|---|---|---|---|",
         "| station_timeseries / device_timeseries | A 处理 (真实 SCADA) | 2026-03-12~09-12 | 1min | 回测/工况/压力基线 |",
         "| forecast_*dispatch/validation/test*_calibrated.parquet | A6 (真实滚动预测, 校准) | 同上分段 | 15min 发布 | B 回测输入 |",
         "| 合成高频喘振 (surge_sim) | 人工生成 | — | 1Hz | 喘振预警评估 |", "",
         "## 2. 窗口切分", "",
         "| 段 | 时间 | 用途 |", "|---|---|---|",
         "| dev | 2026-03-12~07-31 | 训练/开发 |", "| valid | 2026-08-01~08-20 | 选型 |",
         "| test | 2026-08-21~09-12 | 冻结评估 |", "",
         "## 3. 回测口径", "",
         "| 口径 | 定义 | 可否横向比较 |", "|---|---|---|",
         "| full_window | 各策略自有可执行步 | 否 (不同步) |",
         "| paired_exec | 各策略共同可执行步 | 是 |",
         "| service_equiv | 各策略均执行且 flow≥obs−0.05 | 是 (本审计主口径) |", "",
         "样本差异核查: 六策略(b1/b3/bal/lrb/mpc_hist/joint) service=10793 步; 纳入 b3_lrb 后=10789 (其 4 步不可行); **joint_nompc 不改变样本范围**。", "",
         "## 4. KPI 公式 (内部定义)", "",
         "| 指标 | 公式 | 单位 | 备注 |", "|---|---|---|---|",
         "| SEC | ΣE(kWh)/ΣV(m³) | kWh/m³ | E 由 B2 功率模型估计 |",
         "| SEC 改善率 | (SEC_b1−SEC_x)/SEC_b1 | % | 内部定义 |",
         "| 同服务节电率 | (E_b1−E_x)/E_b1, 等体量 V=Σmin(obs,537.8) | % | 内部定义 |",
         "| 加载率偏离度 D | \\|λ4−λ5\\|, λ=Q/268.9 | 百分点(pp) | 仅双机; 单机/停机 NA; 内部定义 |",
         "| 大马拉小车 | 运行且 λ<0.5 的机分钟 | min | 阈值 0.5 内部定义 |",
         "| 压力合格率 | p∈[0.55,0.66] 步占比 | % | 带 CAL-05 工程示例 |",
         "| 压力波动下降 | (std_b1−std_x)/std_b1 | % | 同场景 |",
         "| 喘振提前 | 事件起点−首次告警 | s | 合成仿真 |",
         "| 年成本降低 | 节电率×电价假设(0.7元/kWh) | % | ⑥假设估算 |", "",
         "## 5. 样本过滤与假设", "",
         "- 需求 = SCADA 站级总供气量 (代理需求, 非独立实测用户侧需求)。",
         "- 功率模型: `artifacts/models/power_model.json` (b2-power-v0.1; dev 外推>268.9 占 16.5%)。",
         "- 压力动态: K_VOL=23700 m³/MPa, LEAK=0.001/min (工程假设)。",
         "- 喘振: 真值 q_stab(p)=82+300x−400x² vs B4 代理 90+350x (机制独立)。",
         "- **不同口径结果不可直接比较** (full_window vs service_equiv; 模型回测 vs 合成闭环)。", ""]
    (OUT / "DATA_AND_METRIC_DEFINITIONS.md").write_text("\n".join(l), encoding="utf-8")


def md_failed():
    l = ["# 未达标 / 部分达标 / 缺失指标清单", "",
         "## 1. 未达标 (有计算结果, 不符合目标)", "",
         "| 指标 | 目标 | 实际 | 差距 | 算法/窗口 | 证据 | 原因 | 空间 | 建议 |", "|---|---|---|---|---|---|---|---|---|",
         "| 综合能效 valid/test 单窗口 | ≥6% | valid 3.84% / test 2.98% | ~2–3pp | joint_nompc | TEST_ENERGY_DIAGNOSIS.md | test 高负荷(88%容量)、B1 已高效(0.0902)、10pp 均衡约束(−1.34pp)、模型下界 5.02%/3.91% | 物理+结构限制, 有限 | 不建议为 test 调参 |",
         "| 供气压力合格率 dev | ≥99.5% | 85.92% | ~13.6pp | joint | PRESSURE_DEV_ANALYSIS.md | 13.1% 步需求>2×额定容量(物理) | 容量受限, 无 | 保留如实 |", "",
         "## 2. 部分达标", "",
         "- 综合能效 pooled 7.84% (≥6%) 但分窗口不均; 压力 valid/test 达标、dev 容量受限; 喘振全部为合成仿真; 加载率为内部口径。", "",
         "## 3. 已实现但未测试", "",
         "| 项 | 状态 | 缺什么 |", "|---|---|---|",
         "| C 自然语言理解准确率≥90% | 规则意图引擎已实现 | 带标注 NLU 测试集与准确率统计 |",
         "| 多轮对话≥10轮 | 配置支持 | 端到端多轮验收 |",
         "| 控制指令/下发/反馈/工单时延 | 模拟实现, 有 elapsed | 专项时延计测 |",
         "| 数据读取≤3s / 设备刷新≤5s / 应急≤10s / 维保≤30s | 逻辑存在 | 专项计测 |",
         "| 系统可用率≥99.9% | 未测 | 长稳/压测 |",
         "| 关键决策可解释性评分≥80 | 决策链已实现 | 官方评分表与评分 |",
         "| 数据质量检测准确率≥95% / 工况识别≥90% | 有实现 | 标注集与准确率 |", "",
         "## 4. 缺少必要真实数据 / 未实现", "",
         "| 项 | 原因 |", "|---|---|",
         "| 非计划停机下降≥50% / MTBF≥30% / 异常提前≥24h | 无真实故障与停机记录 (现为注入标签仿真) |",
         "| 设备特性曲线修正误差≤3% | 无厂家特性曲线真值 |",
         "| 喘振真实准确率/提前 | 无真实喘振标签与秒级数据 (现为合成仿真+代理边界) |",
         "| D 前端可视化 (总览/对比/热力图/知识面板) | 团队 main 无前端代码 |",
         "| 主动告警推送≤5s | 未实现 |",
         "| 自适应迭代周期≤7天 闭环 | ARD 在线修正有, 7天闭环与计时证据缺 |",
         "| 方案书/技术总报告/视频/路演/知识产权 | 交付物未完成 |", "",
         "## 5. 是否影响参赛展示", "",
         "- 影响: D 前端缺 -> 可视化与可解释展示无法呈现 (赛题必交项)。",
         "- 影响: test/valid 单窗口能效<6% -> 若评委只看单窗口, 需说明 pooled 与工况差异。",
         "- 不影响: 能效 pooled、低负荷、加载率(内部)、调度时延、容器化、MCP。", ""]
    (OUT / "FAILED_AND_MISSING_METRICS.md").write_text("\n".join(l), encoding="utf-8")


def md_version():
    l = ["# 最终版本与交付核查", "",
         f"## 1. 团队 main 实际采用的算法 ({TEAM_SHA})", "",
         "- 调度: B `optimization/b_api.plan_dispatch` (b3/balanced/load_balanced); C 通过 `integration/dispatch_bridge.py` 调用。",
         "- 安全: B `safety/surge.assess_surge_risk` (代理)。",
         "- A: 预测/健康/RUL/维护服务; K: 知识库; C: FastAPI + MCP(10) + agent; D: 无。", "",
         "## 2. 尚未集成到 main 的实验结果 (B 分支)", "",
         "| 项 | 状态 | 说明 |", "|---|---|---|",
         "| joint / joint_nompc 主调度 | 未接入 B6 | 仅内部候选 (`stage2/joint_dispatch.py`), `simulation_candidate_only` |",
         "| MPC 压力控制 / ML 压力模型 | 未接入 | ML 压力模型零增量, 可不部署 |",
         "| GRU 喘振预警 | 未接入 | 独立候选; B4 仍为在线阻断 |",
         "| DQN/RL | 未接入 | 未超越 MPC, 不部署 |", "",
         "## 3. 关键接口接入状态", "",
         "| 问题 | 结论 |", "|---|---|",
         "| joint_nompc 是否已接入 B6 | **否** (B6 `plan_dispatch` 仍为 b3/balanced/load_balanced) |",
         "| C 是否支持新模式 | C 走 `dispatch_bridge` 传 mode; 需扩 mode 才能用 joint |",
         "| D 引用旧指标 | D 未实现, 无引用 |", "",
         "## 4. 建议保留模块", "",
         "| 模块 | 保留 | 理由 |", "|---|---|---|",
         "| B3 / balanced / LRB | ✅ | 现网稳定, C 已集成 |", "| joint_nompc (调度) | ✅ 候选 | pooled SEC 7.84%, 低负荷 69.8%, 偏离最大 9.999pp |",
         "| MPC (压力) | ✅ 场景层 | step/peak/fault 鲁棒; 真实平滑窗增量≈0 |", "| ML 压力模型 | ❌ | 相对简单模型零增量 |",
         "| GRU 预警 | ✅ 独立模块 | 提前≥30s 覆盖 0.49→0.91 |", "| DQN/RL | ❌ | 未超越 MPC |", "",
         "## 5. 正式推送前剩余阻断项", "",
         "1. D 前端可视化与可解释展示 (赛题必交, 当前缺失)。",
         "2. C 自然语言理解 ≥90% 的验收测试集与准确率证据。",
         "3. 若采用 joint_nompc: 扩 B6/C mode 并完成 C 端到端时延验证。",
         "4. 交付物: 方案书 / 技术总报告 / 演示视频 / 路演 / 知识产权。",
         "5. 现场类指标 (真实停机/喘振/压力) 需现场数据复评, 现阶段仅仿真自证。", ""]
    (OUT / "FINAL_VERSION_AND_DELIVERY_CHECK.md").write_text("\n".join(l), encoding="utf-8")


def ledger_csv():
    ted, lr, cd, robust, surge, s2, pres, preal, pdev, ideal = rows_from_b()
    rows = []
    for w in ("dev", "valid", "test", "pooled"):
        b = ted["pooled"] if w == "pooled" else ted["per_window"][w]
        for s in ("b1_rule", "b3_b4", "bal", "lrb_t10", "joint_nompc", "joint"):
            m = b[s]; d = b["_deviation"][s]
            rows.append({"group": "B", "indicator": "SEC_reduction_pct", "window": w, "algorithm": s,
                         "value": m["sec_reduction_pct"], "unit": "%", "evidence": "final/test_energy_diagnosis.json"})
            rows.append({"group": "B", "indicator": "low_load_reduction_pct", "window": w, "algorithm": s,
                         "value": m["low_reduction_pct"], "unit": "%", "evidence": "final/test_energy_diagnosis.json"})
            rows.append({"group": "B", "indicator": "load_ratio_dev_max_pp", "window": w, "algorithm": s,
                         "value": d["max_pp"], "unit": "pp", "evidence": "final/load_ratio.json"})
            rows.append({"group": "B", "indicator": "load_ratio_dev_mean_pp", "window": w, "algorithm": s,
                         "value": d["mean_pp"], "unit": "pp", "evidence": "final/load_ratio.json"})
    # 压力/喘振/其他
    extra = [
        {"group": "B", "indicator": "pressure_compliance", "window": "dev", "algorithm": "joint", "value": preal["dev"]["counterfactual"]["joint"]["compliance_CAL05"], "unit": "ratio", "evidence": "final/pressure_real_dev.json"},
        {"group": "B", "indicator": "pressure_compliance", "window": "valid", "algorithm": "joint", "value": preal["valid"]["counterfactual"]["joint"]["compliance_CAL05"], "unit": "ratio", "evidence": "final/pressure_real_valid.json"},
        {"group": "B", "indicator": "pressure_compliance", "window": "test", "algorithm": "joint", "value": preal["test"]["counterfactual"]["joint"]["compliance_CAL05"], "unit": "ratio", "evidence": "final/pressure_real_test.json"},
        {"group": "B", "indicator": "surge_accuracy", "window": "test_synth", "algorithm": "GRU_optimized", "value": surge["after_stabilized"]["test"]["time_level"]["accuracy"], "unit": "ratio", "evidence": "stage3/surge/surge_optimized.json"},
        {"group": "B", "indicator": "surge_lead_ge30_coverage", "window": "test_synth", "algorithm": "GRU_optimized", "value": surge["after_stabilized"]["test"]["event_level"]["lead_ge30_coverage"], "unit": "ratio", "evidence": "stage3/surge/surge_optimized.json"},
        {"group": "A", "indicator": "forecast_MAPE_15min", "window": "test", "algorithm": "LightGBM", "value": 1.13, "unit": "%", "evidence": "team:artifacts/metrics/forecast_metrics.json"},
        {"group": "A", "indicator": "rul_within_20pct", "window": "test_sim", "algorithm": "GBR", "value": 88.61, "unit": "%", "evidence": "team:artifacts/metrics/rul_metrics.json"},
        {"group": "A", "indicator": "maint_warning_accuracy", "window": "hybrid_sim", "algorithm": "trend_warning", "value": 92.92, "unit": "%", "evidence": "team:artifacts/metrics/maintenance_metrics.json"},
        {"group": "A", "indicator": "unplanned_stop_reduction", "window": "hybrid_sim", "algorithm": "planned_maintenance", "value": 92.08, "unit": "%", "evidence": "team:artifacts/metrics/maintenance_metrics.json"},
        {"group": "A", "indicator": "mtbf_improvement", "window": "hybrid_sim", "algorithm": "planned_maintenance", "value": 1163.16, "unit": "%", "evidence": "team:artifacts/metrics/maintenance_metrics.json"},
        {"group": "K", "indicator": "kb_top3_hit_rate", "window": "acceptance", "algorithm": "tfidf_char", "value": 88.46, "unit": "%", "evidence": "team:analysis/reports/phaseK/knowledge_acceptance.json"},
    ]
    rows += extra
    (OUT / "kpi_ledger.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "kpi_ledger.csv").open("w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=["group", "indicator", "window", "algorithm", "value", "unit", "evidence"])
        wtr.writeheader(); wtr.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    md_kpi(); md_algorithms(); md_definitions(); md_failed(); md_version(); ledger_csv()
    print("written", OUT)
    for f in sorted(OUT.iterdir()):
        print("  ", f.name)


if __name__ == "__main__":
    main()
