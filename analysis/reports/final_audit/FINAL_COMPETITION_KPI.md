# 空压站多机协同智能调度 Agent —— 全项目最终指标总表

> 审计基准: 团队 main `08915e4 (Wxyun113/Competition main)`; B 组实验分支 `e2c93f9 (feat/b-stage2-metric-optimization)`。
> 数据说明: 需求=真实 SCADA 站级总供气量 (历史代理需求, 非独立实测用户侧需求); 能效=真实历史输入驱动的反事实模型回测(B2 功率模型估计); 压力=真实历史输入驱动动态闭环(工程压力模型); 喘振=1Hz 人工合成事件仿真。
> 判定: 已达标 / 未达标 / 部分达标 / 已实现但未测试 / 缺少必要真实数据 / 未实现 / 无法核查。

## 0. 评价范围 (样本口径) 核对 —— 不得混写

| 口径 | 策略集合 | 运行步(service) | joint_nompc dev/valid/test/pooled SEC降% |
|---|---|---|---|
| **主口径** | b1,b3,bal,lrb_t10,joint_nompc,joint | **10793** | 10.49 / 3.84 / 2.98 / **7.842** |
| 宽口径 | b1,joint_nompc | 11048 | 10.203 / 3.843 / 2.955 / 7.692 |
| 窄口径(含b3_lrb) | +b3_lrb (8策略) | 10789 | — / — / — / 7.833 |

> 差异来源: 纳入更多被比较策略后, 需剔除其不可行步 -> 样本 10793/11048/10789。
> 本报告主表统一采用**主口径 10793**; 稳健性对照另行标注 11048; 贡献消融标注 10789。

## A. 赛题 15 项 KPI (官方目标) —— 全项目现状

| 组 | 指标 | 目标 | 实际 | 算法/模块 | 窗口/场景 | 样本 | 公式 | 数据来源 | 证据等级 | 达标 | 未达原因 | 证据文件 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | 系统综合能效提升率 | ≥6% | pooled 7.842% (dev 10.49/valid 3.844/test 2.978) | joint_nompc (B3+均衡) | dev/valid/test/pooled | 10793 步 | SEC=ΣE/ΣV; 降幅 vs B1 | 真实历史反事实回测 | ②真实驱动模型回测 | 部分达标 | valid/test 单窗口<6%; test 受高负荷+B1已高效+10pp 约束限制 | final/TEST_ENERGY_DIAGNOSIS.md |
| B | 加载率偏离度 | ≤±10% | pooled joint_nompc 均 8.103pp / P95 9.864pp / 最大 9.999pp; 双机达标率 100.0% | joint_nompc | pooled(双机步) | 9026 双机步 | D=|λ4−λ5|(pp), λ=Q/268.9 | 真实历史反事实回测 | ②真实驱动模型回测 | 部分达标(内部口径) | 官方无公式; 内部解释非官方确认 | final/LOAD_RATIO_REPORT.md |
| B | 供气压力合格率 | ≥99.5% | valid 0.9998 / test 0.9957 / dev 0.8592 | joint(含MPC) | dev/valid/test 全窗口 | 各窗 ~5000-40000 步 | p∈[0.55,0.66] 步占比 | 真实压力基线+真实需求闭环 | ③真实驱动闭环仿真 | 部分达标 | dev 13.1% 步需求>容量(物理), 合格率受限 | final/PRESSURE_DEV_ANALYSIS.md |
| B | 喘振预警准确率 | ≥90% | 0.9004 | GRU优化(+B4) | 1Hz 合成 test | 35 事件 | (TP+TN)/N 逐秒; 同时披露 F1/FNR | 人工合成高频仿真 | ④人工合成仿真 | 已达标(仿真) | 非真实喘振标签, 代理边界 | stage3/surge/surge_optimized.json |
| A | 非计划停机次数下降率 | ≥50% | 92.08% (仿真) | A7 维护策略 | 混合仿真 480 episodes | 240 故障/240 正常 | (base-proposed)/base | 真实遥测基底+注入退化标签 | ④人工合成仿真 | 部分达标 | 无真实停机记录; 注入标签仿真 | artifacts/metrics/maintenance_metrics.json |
| B | 调度方案生成时间 | ≤60s | b3 p50 35ms / p95 49ms / max 97ms | B3/joint_nompc | valid 抽测 | 200 | 单次 plan_dispatch 端到端 (算法侧) | 真实回测实测 | ⑤软件测试 | 已达标 |  | analysis/reports/b/kpi_latency.json |
| C | 自然语言指令理解准确率 | ≥90% | 未给出系统化准确率 (仅单元样例) | C agent/intent.py | — | 无标注测试集 | 分类正确率 | — | ⑦尚无有效验证证据 | 已实现但未测试 | 缺带标注的 NLU 测试集 | src/air_station/agent/intent.py |
| B | 单站年用电成本降低率 | 5–10% | ≈8.665% (服务等价节电率年化) | joint_nompc | pooled | 10793 步 | 节电率=(E_b1−E_x)/E_b1; 年化×电价0.7元/kWh(假设) | 真实历史回测+成本假设 | ⑥假设估算 | 部分达标 | 非直接等于 SEC 降幅; 电价/年化为假设 | final/ENERGY_VERIFY.md |
| A | MTBF 提升率 | ≥30% | 1163% (仿真) | A7 | 混合仿真 | 240+240 | (mtbf_p−mtbf_b)/mtbf_b | 真实遥测+注入标签 | ④人工合成仿真 | 部分达标 | 仿真值不可当现场 | artifacts/metrics/maintenance_metrics.json |
| B | 大马拉小车工况时长下降率 | ≥60% | pooled 69.763% (bal 76.0%) | joint_nompc/bal | pooled | 10793 步 | 低负荷机分钟(λ<0.5)降幅 vs B1 | 真实历史反事实回测 | ②真实驱动模型回测 | 已达标 |  | final/b_final_kpi.json |
| B | 压力波动幅度下降率 | ≥30% | dev 45.1 / valid 47.6 / test 92.6% | joint(MPC) | 各窗口 | 同上 | (std_b1−std_joint)/std_b1 | 真实需求驱动闭环 | ③真实驱动闭环仿真 | 已达标(仿真) |  | final/pressure_real_*.json |
| A | 异常预警提前时间 | ≥24h | 中位 30h (仿真) | A7 趋势预警 | 混合仿真 | 240 | 预警时刻→故障时刻 | 真实遥测+注入标签 | ④人工合成仿真 | 部分达标 | 无真实故障标签 | artifacts/metrics/maintenance_metrics.json |
| B/C | 调度策略自适应迭代周期 | ≤7天 | ARD 在线残差修正有; 7天闭环未建立 | B adaptive_robust + C workflow | — | — | 策略更新周期 | — | ⑦尚无有效验证证据 | 部分达标/未实现 | 无在线迭代闭环与计时证据 | src/air_station/optimization/adaptive_robust.py |
| B | 喘振预警提前时间 | ≥30s | 提前≥30s 覆盖 0.914 (有前兆 0.97) | GRU优化 | 1Hz 合成 test | 35 事件 | 事件起点−首次告警; 覆盖=占比 | 人工合成高频仿真 | ④人工合成仿真 | 已达标(仿真) | rapid 无前兆事件 0 提前 | stage3/surge/surge_optimized.json |
| C | 关键决策可解释性评分 | ≥80分 | 决策链已实现; 无评分标尺/前端展示 | C agent/workflow explain_chain | — | — | —(无官方评分表) | — | ⑦尚无有效验证证据 | 已实现但未测试 | 无评分方法; D 前端缺 | src/air_station/agent/workflow.py |

## B. 功能/性能与验收项 (任务书 表格 1-3 + 正文)

| 组 | 要求 | 目标 | 现状 | 达标 | 证据/说明 |
|---|---|---|---|---|---|
| A | 用气负荷预测 MAPE | ≤8% | test MAPE 15/30/60min = 1.13/1.57/2.05% | 已达标 | artifacts/metrics/forecast_metrics.json |
| A | 预测计算时间 | ≤10s | ≈1.4s | 已达标 | forecast 服务 elapsed |
| A | 分钟级/小时级预测 | 支持 | 15/30/45/60min | 已达标 | forecast forecast_features |
| A | 数据质量自动检测 | ≥95% | 有 A1 校验; 准确率未量化 | 已实现但未测试 | analysis/reports/phaseA1 |
| A | 工况状态识别 | ≥90% | 负荷等级/运行状态已实现; 准确率未量化 | 已实现但未测试 | A features |
| A | 设备健康诊断 (健康指数+RUL) | 输出 | 健康无监督 + RUL 仿真(MAE68.6/within20%=88.6%) | 部分达标 | health_metrics.json / rul_metrics.json |
| B | 最优机组组合决策 | ≤30s | b3 max 97ms | 已达标 | kpi_latency.json |
| B | 多目标平衡决策 | ≤60s | 同调度耗时 | 已达标 | 同上 |
| B | 滚动调度周期 | 5–15min | 回测步长 15min; 闭环 5min | 已达标 | backtest/harness |
| B | 压力设定值优化 | 波动降≥30% | 见压力波动 45-93% | 已达标(仿真) | pressure_real_*.json |
| B | 喘振风险分级 + 边界在线修正 | 低/中/高 + 在线 | 分级✅; 在线修正未见 | 部分达标 | safety/surge.py |
| C | 5 个必备 MCP 服务 | 5 | 10 个工具 (含必备5) | 已达标 | mcp_servers/tools.py |
| C | 跨系统工具调用 (SCADA/PLC/EAM/工单) | 支持 | 模拟实现 | 已达标(模拟) | control/ |
| C | 多轮对话 | ≥10轮 | 配置支持; 未验收 | 已实现但未测试 | agent/workflow.py |
| C | 主动告警推送 | ≤5s | 未实现推送 | 未实现 | — |
| D | 可视化 (总览/对比/热力图/知识面板) | 必备 | 无前端代码 | 未实现 | 团队 main 无 vue/tsx/html |
| B/C | 控制指令生成/下发/反馈/工单时延 | ≤5/3/10/30s | 模拟控制有延时字段; 未专项计测 | 已实现但未测试 | control/ + 返回 elapsed |
| A/C | 数据读取延迟 | ≤3s | 本地 Parquet; 未专项计测 | 已实现但未测试 | data/ |
| A/C | 设备状态刷新延迟 | ≤5s | API 实时读; 未计测 | 已实现但未测试 | api/ |
| B | 异常工况应急响应 | ≤10s | 安全联锁即时(模拟); 未专项计测 | 已实现但未测试 | control/interlock.py |
| B | 维保计划决策 | ≤30s | A7 维护逻辑存在; 未计测 | 已实现但未测试 | phaseA7 |
| A/D | 设备特性曲线修正误差 | ≤3% | 无真值曲线 | 缺少必要真实数据 | — |
| C | 系统可用率 | ≥99.9% | 未测 | 已实现但未测试 | — |
| C | 容器化部署 | 支持 | Dockerfile + compose (api+mcp) | 已达标 | deployment/ |
| B | B01–B20 验收测试 | 20 项 | 20 passed (内部验收集) | 已达标 | tests/optimization/test_b01_b20_acceptance.py |
| C | 知识库问答 (K) | Top-3 命中 | Top-3 88.46%, 拒答100%, 引用100%, p95 0.066s | 已达标 | analysis/reports/phaseK/knowledge_acceptance.json |