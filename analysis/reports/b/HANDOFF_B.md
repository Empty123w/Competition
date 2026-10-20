# 成员 B 当前状态交接文档（新 AI 任务可直接读取）

- 生成日期：2026-10-18
- 项目：空压站多机协同智能调度 Agent
- 用途：本轮收尾交接；**不开启新研究、不重复运行已完成实验**

---

## 1. 分支与提交

| 项 | 值 |
|---|---|
| 当前分支 | `feat/b-optimization-aligned` |
| 本地 HEAD | `cd65c21 feat(b4): implement surge risk proxy and safety boundaries` |
| 远程 HEAD | `cd65c21`（`refs/heads/feat/b-optimization-aligned`；经 `git ls-remote` 核实） |
| 已推送范围 | **仅到 B4**：`de603f9`(B0) → `1e28e6a`(B1) → `4299356`(docs) → `29482d7`(B2) → `8e34c15`(B3) → `cd65c21`(B4) |
| **未推送成果** | **B5 全部结果、KPI 台账、B5 差距分析、三项改进实验、算法实验（E1/E2）均在本地未提交/未推送**（`git ls-remote` 远程仍为 `cd65c21`） |
| 工作区 | 除 `.cosmocode/`（不入库）外，B5 及实验产物**均未提交** |

> 更正：此前“已推送远程 HEAD = cd65c21，此前已推送”易被误读为“B5/KPI/实验已推送”。实际**远程最后确认提交为 B4 的 `cd65c21`**；B5/KPI/实验**未提交也未推送**。

**未提交文件（全部在 `analysis/` 与 `tests/b5/`）**：
- `analysis/scripts/`：`b3_compare.py`、`b5_*.py`（backtest/gap/residual/state/postprocess/verify/trace）、`improvement_experiments.py`、`algorithm_experiments.py`、`multistep_optimization.py`、`load_ratio_definition.py`、`pressure_dynamics.py`、`surge_simulation_validation.py`
- `analysis/reports/b/`：`b5_*`、`improvement_*`、`algorithm_experiments*`、`competition_kpi_gap_ledger.md`、`energy6pct_dependency_list.md`、`kpi_priority_next_research.md`、`data_requirements_b.md`、`metrics_dictionary_b.md`、`optimization_*`、`performance_report.md`、`pressure_report.md`、`surge_*`、`B3_validation_audit.md` 等
- `tests/b5/`（新增 4 项状态语义测试）

> 未执行任何新增 Git 提交、未 push/合并/PR（用户明确禁止）。

## 2. 冻结基线（必须保留，不得修改）

| 模块 | 状态 |
|---|---|
| B1 `src/air_station/baseline/*` | 冻结 |
| B2 `optimization/{performance,pressure}.py` + 模型工件 | 冻结 |
| B3 `optimization/{constraints,optimizer,fallback,service}.py` | 冻结 |
| B4 `safety/{boundaries,surge}.py` + `configs/safety.yaml` | 冻结 |
| B5 结果（3.87% / 3.66% / 4710 步） | 冻结，保留（本地未提交/未推送） |
| A 的代码/数据/模型 | 只读，不改 |

## 3. 两套集合必须分开记录（勿混用）

**（A）B5 集合（冻结结果，窗口 2026-08-21~09-11）**
- 共同容量可行 **4735** → B4 风险保留 **4712**（−23 `unknown` 阻断）→ 模型约束下可模拟执行 **4710**（−2 过渡缺口）。
- **最终 4710 步中含 142 个 `medium` 待现场复核**（`medium` 仅提示、不阻断；`block_on_high/unknown=true`）。
- 详见 `b5_state_reconciliation.md`、`b5_backtest_report.md`。

**（B）开发/验证实验集合（E1/E2/改进实验，窗口 2026-03-12~08-20）**
- 容量可行 **1919** → 风险过滤后可模拟执行 **1918**（**1 个 `unknown` 被阻断，不计入安全可执行/节能**）。
- 该集合含 **`medium` 692 / `low` 1226 / `unknown` 1**。
- 详见 `improvement_experiments_b.md`、`algorithm_experiments_ledger.csv`（1919 行）。

> 更正：**4735→4712→4710（含 142 `medium`）属 B5 集合**；**1919→1918（含 692 `medium`、1 `unknown` 阻断）属开发/验证实验集合**。两套数字此前被混记，现分列，勿互相引用。

## 3.1 本轮两项算法实验（已完成，勿重跑）

脚本：`analysis/scripts/algorithm_experiments.py`（运行 208.6 s）
产物：
- `analysis/reports/b/algorithm_experiments_b.md`
- `analysis/reports/b/algorithm_experiments.json`
- `analysis/reports/b/algorithm_experiments_ledger.csv`（1919 行）
- `analysis/reports/b/power_model_exp_v2.json`（S2 实验模型，独立文件，不覆盖冻结工件）

**证据范围**：开发 2026-03-12~07-31 / 验证 2026-08-01~08-20；任务=实测总流量（nowcast，仅实验）；`extra 5%` 冻结；**未使用 B5 窗口 08-21~09-11**；临时预测仅用过去信息。**非 A 冻结口径、非现场、非达标证据。**

**口径区分（重要）**：
- E1 `+0.012%` 是**相对 B3 冻结方案**的电量差（百分比，基于 B3 同一评价模型），**不是**相对 B1 的节电率，也**不能**与 B5 的“3.87%”直接相加或比较。
- B5 的 `3.87%`（电量降幅）/`3.66%`（SEC）是**相对 B1 基线**在同一 4710 步上的降幅；若换算，E1 相对 B3 的 `+0.012%` 在 B3 的 3.87% 基础上仅约 **+0.01 个百分点**，且量级可忽略。

### E1 分段功率曲线负荷分配
- 实现：逐机单拐点分段线性 `power~flow` 拟合（开发段），同枚举+贪心分配；用**同一冻结评价模型**评估。
- 验证误差（vs 冻结多变量基线）：
  - 4#：MAE **13.19 → 21.97**（变差）
  - 5#：MAE **11.36 → 37.29**（显著变差）
- 分配结果（1919 步，同任务/同时间步）：B3 冻结 **1,126,392.17 kWh / SEC 0.090223**；E1 **1,126,254.25 kWh / SEC 0.090212**；E1 vs B3 **+0.012%**（可忽略）。
- 供气：B3 12,484,529.1 m³；E1 12,484,530.9 m³（差 ~1.8 m³）。
- 结论：**变差/无有效增益**。

### E2 “多时间步滚动”——实为**最大需求选组合的启发式实验**（非完整多时间步优化）
- **准确定性**：E2 是 **horizon-max 启发式**——用预测时域内的**最大需求**决定机组组合，当前步再分配；这是**选组合启发式**，**尚未**实现完整的多时间步优化（未做跨步的时域联合决策/目标函数滚动）。
- 实现：季节日临时预测（前一日同时刻，仅过去信息）；myopic vs horizon-max rolling vs oracle（真实未来最大值，仅诊断）。
- 结果（1918/1919 步）：
  - myopic：1,120,973.01 kWh
  - horizon-max rolling：1,125,079.03 kWh → **+0.37%（更差）**，18 步组合变化
  - oracle（诊断）：1,130,360.27 kWh → +0.79%（更差）
- **供气账本缺失**：实验账本**未记录 E2 各计划的供气量**，故 E2 的 **SEC 与供气守恒不可评估**（如实说明，不补造）。
- **候选未做 B4 评估**：E1/E2 规划器仅用冻结容量/备用/加载上下限；**B4 喘振评估只对冻结 B3 运行，未对 E1/E2 的候选计划单独评估 → 其喘振安全性未验证**（不得视为安全通过）。
- 结论：**该启发式形态**在冻结约束+临时预测下无收益、反而更差；**完整多时间步优化尚未开展**（见 §3.2，现已完成）。

## 3.2 完整多时间步优化实验（已完成，勿重跑）

脚本：`analysis/scripts/multistep_optimization.py`（运行 295.7 s）
产物：`multistep_optimization_b.md`、`multistep_optimization.json`、`multistep_optimization_ledger.csv`（1920 行）
- 方法：**exact 枚举 4^H 组合序列**（H=4×15 min=60 min）的**完整时域规划**，目标=时域总电量+切换惩罚。
- **补齐旧 E2 缺失**：完整**供气账本**（计划/执行供气、电量、SEC）＋**每步执行候选均做 B4 喘振评估**。
- 公平步集（1691 步，四规划器均成功且可执行）：

| 规划器 | 电量(kWh) | SEC | 相对 B1 节电率 | 缺供(vs实测,m³) |
|---|---|---|---|---|
| B1 规则 | 1,072,527.73 | 0.097778 | — | 280,955.75 |
| myopic（H=1 对照） | 989,080.63 | 0.090318 | 7.78% | 298,856.72 |
| **multi_step（H=4）** | 989,322.68 | 0.090325 | 7.76% | 297,056.72 |
| oracle（诊断） | 997,964.96 | 0.090347 | 6.95% | 0.06 |

- **multi_step vs myopic = −0.0245%**（无电量增益，略差）；**覆盖 +228 步**（1920 vs myopic 1692，多步用未来步吸收被锁定的切换）。
- B4：每步候选均评估，`unknown` 阻断 1 步/规划器；`medium` 615–724 步（仅复核、不阻断）。
- 结论：多步的收益是**可行性覆盖**而非节能；预测质量主导（季节日低估→缺供约 28–30 万 m³）。
- **口径**：`7.76%`/`7.78%` 是相对 B1 的降幅（本实验 nowcast/季节日口径），**不等于** B5 冻结 KPI `3.87%`；两者不可混加。

## 4. Bad Gateway / 执行中断核查

- 本轮记录中**未观察到“Bad Gateway”错误**；`algorithm_experiments.py`（208.6 s）与 `multistep_optimization.py`（295.7 s）均正常结束。
- 已核验产物完整：`algorithm_experiments.json` 可解析、账本 1919 行；`multistep_optimization.json` 可解析、账本 1920 行；报告与脚本均存在。**无需补齐。**

## 5. 关键 KPI 现状（详见 `competition_kpi_gap_ledger.md`）

- **6% 综合能效：未完成**；B5 模型预计电量降幅 **3.87%**、SEC **3.66%**，适用 **4710 步**；3.87% **不是空间上限**。
- 大马拉小车下降：4710 步 **41.4%**（700→410 min；达 60% 需 B3 ≤280 min）。
- **压力**：数据**存在**（train 203,994 / valid 28,788 / test 31,671）；冻结模型为 **persistence**，验证 MAE 0.00212 MPa、test MAE 0.00296 MPa。**建模状态**：现有数据仅支持**短时持续性**预测（lag-1 自相关 0.9866，Δp 与流量变化相关 −0.0174），**缺“调度动作反事实压力响应”证据**（需管网机理模型或专项激励实验），故优化后压力合格率**只能作仿真指标**，**非真实控制效果**。参考带 `[0.55,0.66] MPa` 为待确认示例（CAL-05）。
- 喘振准确率：合成秒级仿真（事件级）冻结 B4 精确率 0.996 / 召回 0.939（5/5 检出）；**提前时间 lead≥30s 覆盖 0%**；**独立早预警层**（新算法, band=40）仿真 lead≥30s 覆盖 **1.0**。均**非现场**。
- 加载率偏离度：已实现工程定义与验证（`load_ratio_definition_b.md`）。B3 计划整体超标：口径A(额定) **51.0%**、口径B(效率平台下界) **47.1%**；**CAL-04 目标口径未确认**。
- 大马拉小车（本轮同窗）：fair 1691 步下降 **15.3–15.5%**；服务等价 834 步 **0%**。旧 B5 700→410（41.4%）**未用于本轮调参**。
- 调度耗时：B 求解核心 n=1582，p50 0.0303 / p95 0.0397 / p99 0.0485 / max 0.0790 s；C 全链路未测。
- 约 7.1% nowcast 实验**不是达标证据**。

## 6. 下一步

1. **完整多时间步优化已完成**（§3.2）：结论为“多步无电量增益、收益在可行性覆盖”，已补齐供气账本与候选 B4 评估。
2. 保持口径：`extra 5%` 冻结、安全约束不放松、**不在 B5 窗口调参**；区分“相对 B3/myopic 的百分比改善”与“相对 B1 节电率增加的百分点”。
3. 获取 A 的 dev/valid **无泄漏同口径预测**后，在独立窗口对多时间步优化做二次验证；届时可评估 MILP 形式与需求口径（CAL-02）。
4. B 提出 CAL-02/03/04/05 方案并与 C/工艺/主办方确认；**未确认前继续冻结 `extra 5%`，不改 N-1、不取消备用**。
5. B 自身：压力响应建模条件检查（现数据仅支持短时持续性）、加载率指标实现、喘振代理完善。
6. 新独立最终测试时段或 A 指定未接触保留段后再做最终验证。
7. B6 **暂缓**。

## 7. 约束（执行任何后续任务必须遵守）

- 保留冻结 B1–B5；**不得用 B5 测试窗口（2026-08-21~09-11）调参或选策略**。
- 不放松供气、备用、压力、喘振安全约束；不擅自改需求/备用口径。
- 不重复运行已完成实验；不覆盖原始结果。
- **不执行 Git push、远程合并、创建 PR**。
- 所有结论标注证据类型；离线模型改善 ≠ 现场综合能效证明。

## 8. 七项指标统一任务产物（本轮新增，本地未提交）

| 部分 | 脚本 | 报告 | 数据/模型 | 测试 |
|---|---|---|---|---|
| 一 多步联合能效+大马拉小车 | `analysis/scripts/multistep_optimization.py`（已扩充服务等价/逐机） | `multistep_optimization_b.md` | `multistep_optimization.json`、`*_ledger.csv`、`multistep_extra_steps_trace.csv` | `tests/b5/test_multistep_dp.py`、`tests/b5/test_multistep_extra_steps.py` |
| 二 加载率 | `analysis/scripts/load_ratio_definition.py` | `load_ratio_definition_b.md` | `load_ratio_definition.json`、`load_ratio_ledger.csv` | — |
| 三 压力动态 | `analysis/scripts/pressure_dynamics.py` | `pressure_dynamics_b.md` | `pressure_dynamics.json` | — |
| 四 喘振仿真验证 | `analysis/scripts/surge_simulation_validation.py` | `surge_simulation_validation_b.md` | `surge_simulation_validation.json` | — |
| 总表 | — | `seven_kpi_status_b.md` | — | — |
| 五 A 预测适配器 | — | — | `src/air_station/optimization/forecast_adapter.py` | `tests/optimization/test_forecast_adapter.py` |
| 本轮 全窗口核算 | `analysis/scripts/full_window_accounting.py` | `energy6pct_full_window_b.md` | `full_window_accounting.json` | — |
| 本轮 DP v2 | `analysis/scripts/multistep_optimization_v2.py` | `bigsmall_and_dp_v2_b.md` | `multistep_v2.json`、`multistep_v2_ledger.csv` | — |
| 本轮 压力仿真 | `analysis/scripts/pressure_sim_dynamics.py` | `pressure_sim_dynamics_b.md` | `pressure_sim_dynamics.json`、`_ledger.csv` | — |
| 本轮 EW 完整验证 | `analysis/scripts/surge_ew_validation_v2.py` | `surge_ew_validation_v2_b.md` | `surge_ew_validation_v2.json` | — |

- 全量回归 **187 passed**。A 预测到货后：用 `forecast_adapter.py` 接入，运行同口径连续对照（B1 / 冻结 B3+B4 / myopic / 多步 / 本轮候选）。

- 全量回归：`tests/data models baseline optimization safety b5` = **180 passed**。
- 本部分关键更正：多步**服务等价口径**（防“少供气=省电”误判）；**逐机**大马拉小车（此前误用总流量）；缺供**归因预测低估**。
- 纪律：A 正式预测未到，用仅过去信息临时预测；验证窗口被重复查看，**不宣称独立最终测试**；不推送/合并/PR；B6 暂缓。

---

**交接文档准确路径**：`/workspace/analysis/reports/b/HANDOFF_B.md`
