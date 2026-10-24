# 空压站多机协同智能调度 Agent

面向工业空压站多机协同运行场景的智能调度项目。系统以真实历史运行数据为基础，融合数据治理、用气负荷预测、设备健康诊断、优化调度、安全约束和多智能体协同能力，目标是在满足供气压力和设备安全要求的前提下，降低空压站能耗并提高运行可靠性。

本项目为竞赛项目，当前 **A、B、C 三模块已完成**（A：数据/预测/健康/维护/RUL/知识库；B：基线/优化/安全；C：后端/MCP/Agent/控制/部署），**D（前端/演示）待开发**。

---

## 1. 赛题背景

工业空压站通常由多台空气压缩机共同承担供气任务。传统运行方式主要依靠固定规则和人工经验，容易出现：

- 多台设备加载分配不合理；
- 用气负荷变化时响应不及时；
- 母管压力波动或长期高压运行；
- 高能耗设备仍被优先加载；
- 设备异常状态没有及时参与调度决策；
- 预测、优化、健康诊断和控制系统之间缺少统一接口。

本项目围绕“预测—诊断—优化—执行—反馈”闭环，构建可解释、可重复运行的空压站多机协同智能调度原型。

## 2. 项目目标

项目最终目标包括：

1. 对原始设备、管网、负荷、环境、事件和维护数据进行统一治理；
2. 预测未来 15、30、60 分钟用气负荷；
3. 评估 4#、5# 空压机的健康状态和风险等级；
4. 构建历史人工运行基线；
5. 在压力、能耗、设备能力和健康风险约束下生成多机调度方案；
6. 通过 MCP、API 和 Agent 工作流对预测、诊断、优化与控制模拟能力进行编排；
7. 使用前端页面展示实时状态、预测结果、调度建议、节能效果和告警信息；
8. 保证所有指标、数据来源和验证模式可追溯，不将模拟结果表述为真实结果。

## 3. 系统总体流程

```text
原始运行数据
    ↓
数据校验、清洗与标准化
    ↓
站级/设备级标准时序数据
    ├──────────────┐
    ↓              ↓
负荷预测        健康诊断
    └──────┬───────┘
           ↓
历史基线 + 多机优化调度 + 安全约束
           ↓
API / MCP / Agent 协同编排
           ↓
前端展示与控制模拟
           ↓
执行反馈、指标评估与策略改进
```

## 4. 当前完成状态

| 模块 | 负责人 | 状态 | 主要内容 |
|---|---|---|---|
| A0 接口与配置 | A | ✅ 完成 | 标准字段、数据质量、预测和健康接口约定 |
| A1 原始数据校验 | A | ✅ 完成 | 文件、字段、类型、时间范围和哈希检查 |
| A2 数据标准化 | A | ✅ 完成 | 站级、设备级、事件和维护表 |
| A3 特征工程 | A | ✅ 完成 | 预测特征和健康特征 |
| A4 负荷预测 | A | ✅ 完成 | 15/30/60 分钟预测、区间和评估 |
| A5 健康诊断 | A | ✅ 完成 | 无监督评分、风险等级和建议 |
| A6 滚动回放预测 | A | ✅ 完成 | 无泄漏扩展窗口、共形区间校准、45/75 分钟、历史健康/RUL |
| A7 预测性维护 | A | ✅ 完成 | 真实遥测+注入退化混合仿真；`maintenance_json()`(hybrid_simulation) |
| K 知识库构建 | A | ✅ 完成 | 10 类 12 卡、TF-IDF 检索、MCP/API/Agent 接入、拒答与 stale |
| 历史人工基线 | B | ✅ 完成 | 历史策略还原、能耗和压力指标 |
| 优化与安全 | B | ✅ 完成 | 多机调度(b3/balanced/load_balanced)、能耗目标、压力与喘振安全约束 |
| 后端与 MCP | C | ✅ 完成 | API、MCP（含 RUL）、Agent、模拟控制闭环、数据库；调度/喘振调用 B 接口 |
| 前端与演示 | D | ⏳ 待开发 | 可视化页面、场景演示、PPT和视频 |

> README 只将已经实现并验证的内容标记为完成，未完成模块不会提前宣称具备正式效果。
> C 的调度与喘振直接调用 B 的稳定接口 `air_station.optimization.b_api`
> （`plan_dispatch` / `assess_surge_risk`），B 输出恒为 `simulation_candidate_only`；
> 端到端执行经 C 的准入闸门，仅 `candidate_usable` 的方案才在模拟环境下发。

## 5. 数据说明

### 5.1 原始数据

```text
data/
├─ 设备运行参数.csv
├─ 设备状态监测.csv
├─ 用气负荷数据.csv
├─ 管网运行数据.xlsx
├─ 环境参数.xlsx
├─ 运行事件记录.xlsx
├─ 维护保养记录.xlsx
├─ 设备档案.json
└─ 指标释义.json
```

数据覆盖约 184 天，时间范围为 2026-03-12 至 2026-09-12，主要采用 1 分钟粒度，包含 4#、5# 两台离心式空压机。

### 5.2 处理后数据

```text
data/processed/
├─ station_timeseries.parquet
├─ device_timeseries.parquet
├─ events.parquet
├─ maintenance.parquet
├─ forecast_features.parquet
├─ health_features.parquet
├─ data_manifest.json
└─ feature_manifest.json
```

当前仓库已经包含六个处理后 Parquet，团队成员克隆后可以直接运行预测和健康接口。

处理后数据仍可通过流水线从原始数据重复生成。原始文件视为只读数据，禁止人工修改。

### 5.3 关键数据质量处理

- B 相电流恒为 0，不作为有效模型特征；
- 4# 维护计数器中的 `65535` 和约 `4.29e9` 按哨兵值处理；
- 环境温湿度短时掉点可以标记并插值；
- 冷却器温度前约 6 天的长缺失不进行虚假插补；
- 加卸载列全空、预警列恒 0，不伪造事件；
- 故障码 26 仅作为弱标签，并标记可能存在站级共因；
- 所有真实、推断和模拟数据应使用明确来源标记。

## 6. 目录结构

```text
Competition/
├─ analysis/                    # 数据分析、阶段报告和交接文档
├─ artifacts/
│  ├─ models/                  # 预测模型
│  ├─ metrics/                 # 预测与健康指标
│  └─ logs/
├─ configs/                    # 数据、预测和健康配置
├─ data/
│  └─ processed/               # 标准数据与模型特征
├─ src/air_station/
│  ├─ data/                    # 数据流水线
│  └─ models/
│     ├─ forecasting/          # 负荷预测
│     └─ health/               # 健康诊断
├─ tests/
│  ├─ data/
│  └─ models/
├─ pyproject.toml
├─ README.md
└─ 赛题详情.docx
```

B/C/D 相关目录（多数已实现，frontend/demo 待开发）：

```text
src/air_station/baseline/        # 已实现(B)
src/air_station/optimization/    # 已实现(B)
src/air_station/safety/          # 已实现(B)
src/air_station/api/             # 已实现(C)
src/air_station/mcp_servers/     # 已实现(C)
src/air_station/agent/           # 已实现(C)
src/air_station/control/         # 已实现(C)
src/air_station/db/              # 已实现(C)
src/air_station/knowledge/       # 已实现(K, A)
deployment/                      # 已实现(C)
frontend/                        # 待开发(D)
demo/                            # 待开发(D)
```

## 7. 环境要求

- Python 3.11 或更高版本；
- 推荐使用独立虚拟环境；
- 依赖由 `pyproject.toml` 管理；
- 预测模型运行需要 LightGBM；
- 自动化测试需要 pytest。

## 8. 安装方法

### Windows

```powershell
git clone https://github.com/Wxyun113/Competition.git
cd Competition

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果不能激活虚拟环境，可以直接执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### Linux/macOS

```bash
git clone https://github.com/Wxyun113/Competition.git
cd Competition

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

检查安装：

```bash
python -c "import air_station; print('air_station import OK')"
python -c "import pandas, pyarrow, sklearn, lightgbm; print('dependencies OK')"
```

## 9. 快速运行

### 9.1 数据校验

```bash
python -m air_station.data.pipeline --stage validate --input data
```

### 9.2 重新生成标准数据

仓库已包含处理后数据，普通使用不需要执行本步骤。只有需要验证可重复性或原始数据发生正式变更时才执行：

```bash
python -m air_station.data.pipeline --stage standardize --input data
python -m air_station.data.pipeline --stage features --input data
```

### 9.3 运行负荷预测

```bash
python -m air_station.models.forecasting.service
```

或在 Python 中调用：

```python
from air_station.models.forecasting.service import forecast_json

result = forecast_json()
print(result)
```

### 9.4 运行健康诊断

```bash
python -m air_station.models.health.service
```

或在 Python 中调用：

```python
from air_station.models.health.service import health_json

result = health_json()
print(result)
```

### 9.5 重新生成健康指标和报告

```bash
python -m air_station.models.health.evaluate
```

固定输出：

```text
artifacts/metrics/health_metrics.json
analysis/reports/phaseA5/health_report.md
```

### 9.6 启动后端 / MCP / Agent（C 模块）

```bash
# 启动后端 API (根路径 / 返回能力清单, /docs 为接口文档)
air-station-api

# 启动 MCP 服务 (stdio; --http 为 streamable-http, 默认端口 8765)
air-station-mcp

# 命令行 Agent 编排 (调度与喘振调用 B 接口)
air-station-agent "优化调度并执行"
```

容器化：

```bash
docker compose -f deployment/docker-compose.yml up --build
```

C 模块 API、MCP 工具、控制闭环与安全边界详见
[`analysis/reports/phaseC/README.md`](analysis/reports/phaseC/README.md)。

## 10. 负荷预测接口

唯一对外入口：

```python
forecast_json(
    processed_dir="data/processed",
    config_path="configs/forecasting.yaml",
    model_dir="artifacts/models",
    horizons=None,
) -> dict
```

默认预测时域：

```text
15分钟、30分钟、60分钟
```

输出示例：

```json
{
  "status": "success",
  "model_type": "LightGBM",
  "prediction_base_time": "2026-09-12T00:00:00",
  "predictions": [
    {
      "horizon_minutes": 15,
      "target_time": "2026-09-12T00:15:00",
      "predicted_flow": 499.18,
      "lower_bound": 483.75,
      "upper_bound": 512.86
    }
  ],
  "elapsed_seconds": 1.54
}
```

示例中的预测值仅用于说明字段结构，正式系统必须使用实际函数返回结果。

## 11. 健康诊断接口

唯一对外入口：

```python
health_json(
    processed_dir="data/processed",
    thresholds_path="configs/health_thresholds.yaml",
) -> dict
```

成功响应的顶层字段固定为：

```text
status
validation_mode
evaluation_time
devices
```

每台设备固定包含：

```text
device_id
health_score
risk_level
anomaly_score
top_anomaly_factor
current_value
normal_range
trend_direction
suggestion
```

主要约定：

- `validation_mode` 固定为 `unsupervised`；
- `evaluation_time` 使用 UTC ISO 8601 的 `Z` 格式；
- 设备顺序稳定为 `4#`、`5#`；
- 风险等级为 `正常/关注/预警/严重`；
- 趋势为 `平稳/改善/恶化`；
- 成功响应字段不能由后端或前端自行修改。

## 12. 测试

运行：

```bash
# A / A6 / A7
python -m pytest tests/data tests/models -q
# B
python -m pytest tests/baseline tests/optimization tests/safety -q
# C
python -m pytest tests/control tests/mcp tests/agent tests/db tests/api -q
# K 知识库
python -m pytest tests/knowledge -q
# 全量
python -m pytest tests -q
```

当前交付基准：

```text
全量: 402 passed，0 skipped
（分块：A/A6/A7 含 tests/data+tests/models；K 为 tests/knowledge 20 项）
```

接收环境必须重新运行测试。如果出现大量 `skip`，应检查 Parquet、模型和依赖是否完整，不能把跳过测试当作通过。
注意：一次性运行全部测试在内存受限环境可能触发 OOM（进程被 SIGKILL，退出码 137），建议分块运行。

## 13. 当前模型指标

### 13.1 负荷预测

指标文件：

```text
artifacts/metrics/forecast_metrics.json
```

| 时域 | MAPE | MAE | RMSE | WAPE |
|---|---:|---:|---:|---:|
| 15分钟 | 1.13% | 4.996 m³/min | 18.975 m³/min | 1.06% |
| 30分钟 | 1.57% | 7.038 m³/min | 22.611 m³/min | 1.49% |
| 60分钟 | 2.05% | 10.196 m³/min | 28.554 m³/min | 2.16% |

当前结果满足 MAPE 不高于 8% 的项目目标，但没有优于最强的短期 `lag_1` 基线。因此项目可以说明“达到预测精度目标”，不能说明“已全面优于所有简单基线”。

### 13.2 健康诊断

指标文件：

```text
artifacts/metrics/health_metrics.json
```

健康诊断采用无监督方法。故障码 26 仅为事件级弱标签，两台设备的事件高度同步，可能属于站级共因。目前弱标签分离度为负，必须如实保留。

项目不得宣称：

- 已获得真实故障分类准确率；
- 已具备精确 RUL；
- 健康评分可以直接替代人工检修判断；
- 风险等级可以直接触发真实设备停机。

### 13.3 预测性维护（A7，混合仿真）

指标文件：`artifacts/metrics/maintenance_metrics.json`；报告：`analysis/reports/phaseA7_maint/maintenance_report.md`。

| 指标 | 目标 | 结果 | 口径 |
|---|---:|---:|---|
| 非计划停机下降 | ≥50% | 92.08% | hybrid_simulation |
| MTBF 提升 | ≥30% | 1163.16% | hybrid_simulation |
| 异常预警准确率 | ≥90% | 92.92%（P=0.94,R=0.92） | hybrid_simulation |
| 预警提前（中位） | ≥24h | 30.0h | hybrid_simulation |

> 口径：真实遥测作底 + 注入退化标签（`label_source=injected_degradation`），证明方法链路与仿真下达标，不等同现场实测。

### 13.4 知识库（阶段 K）

指标文件：`analysis/reports/phaseK/knowledge_acceptance.json`。

| 验收项 | 要求 | 实测 |
|---|---:|---:|
| 文档 / 类别 / 问题集 | ≥10 / 10 / ≥30 | 12 / 10 / 30 |
| Top-3 命中率 | ≥85% | 88.46% |
| 成功回答引用覆盖 | 100% | 100% |
| 无依据拒答率 | ≥90% | 100% |
| 检索 P95 | ≤2s | <0.1s |

## 14. 团队分工

### A：数据、预测和健康诊断

- 维护数据流水线和标准字段；
- 维护负荷预测和健康诊断接口；
- 保证模型、指标和接口可追溯；
- 配合 B、C 完成首次接入。

### B：基线、优化和安全约束

- 构建历史人工控制基线；
- 实现能耗、压力和设备约束；
- 接入预测和健康结果；
- 生成多机调度方案；
- 完成优化前后指标比较。

### C：后端、MCP和Agent

- 建设 API 和数据库；
- 封装预测、健康、调度和控制 MCP 工具；
- 建立 Agent 工作流；
- 定义统一错误响应；
- 使用模拟 PLC/SCADA 完成执行闭环。

### D：前端、演示和材料

- 建设监控与调度页面；
- 展示预测、健康、调度和节能效果；
- 编写端到端测试；
- 准备场景、截图、PPT和演示视频。

详细协作和交接规则见：

[项目协作与A模块详细交接说明.md](项目协作与A模块详细交接说明.md)

## 15. GitHub协作规则

建议分支：

```text
feat/a-data-models
feat/b-optimization
feat/c-backend-agent
feat/d-frontend-demo
```

基本原则：

- 不直接向 `main` 推送；
- 每项功能使用独立分支；
- 通过 Pull Request 合并；
- 至少一名相关成员审查；
- 修改公共接口必须更新文档和测试；
- 禁止 force push 到 `main`；
- Parquet 仅由 A 更新，其他成员只读。

## 16. 重要文档

| 文档 | 用途 |
|---|---|
| [`analysis/reports/HANDOFF.md`](analysis/reports/HANDOFF.md) | 总体交接索引 |
| [`analysis/reports/A_to_B_数据接口.md`](analysis/reports/A_to_B_数据接口.md) | B的数据与风险接口 |
| [`analysis/reports/A_to_C_服务接口.md`](analysis/reports/A_to_C_服务接口.md) | C的函数与服务接口 |
| [`analysis/reports/phaseA0/标准字段清单.md`](analysis/reports/phaseA0/标准字段清单.md) | 站级和设备级字段标准 |
| [`analysis/reports/phaseA0/接口字段清单.md`](analysis/reports/phaseA0/接口字段清单.md) | 预测与健康输出标准 |
| [`analysis/reports/phaseA5/health_report.md`](analysis/reports/phaseA5/health_report.md) | 健康诊断方法、指标和局限 |
| [`四人并行开发与集成方案.md`](四人并行开发与集成方案.md) | 四人协作与验收流程 |
| [`analysis/reports/phaseC/README.md`](analysis/reports/phaseC/README.md) | C 模块总览（API/MCP/Agent/控制） |
| [`analysis/reports/phaseC/API接口说明.md`](analysis/reports/phaseC/API接口说明.md) | 后端 REST 接口 |
| [`analysis/reports/b/integration_B_to_C.md`](analysis/reports/b/integration_B_to_C.md) | B→C 调度接口与 C 侧接入 |
| [`analysis/reports/团队进度总览.md`](analysis/reports/团队进度总览.md) | 团队级进度对照 |
| [`analysis/reports/赛题要求对照与总进度.md`](analysis/reports/赛题要求对照与总进度.md) | 逐条赛题对照（含功能/性能指标） |
| [`analysis/reports/phaseA7_maint/maintenance_report.md`](analysis/reports/phaseA7_maint/maintenance_report.md) | 预测性维护仿真（四指标） |
| [`analysis/reports/phaseK/README.md`](analysis/reports/phaseK/README.md) | 知识库来源/架构/运行/限制 |
| [`analysis/reports/phaseK/A_to_D_知识问答API.md`](analysis/reports/phaseK/A_to_D_知识问答API.md) | 知识问答 API（D 接入） |

## 17. 安全边界

当前项目属于竞赛原型：

- 不直接连接真实 PLC 下发控制命令；
- 不绕过设备保护逻辑；
- 不自动执行真实停机；
- 所有控制能力先通过模拟接口验证；
- 真实工业部署前必须经过设备厂商、工艺、安全和运维人员审核。

## 18. 知识产权与数据说明

- 项目代码、第三方依赖和模型应遵守各自许可证；
- 主办方数据仅用于授权范围内的竞赛和验证；
- 未经确认不得公开敏感工业数据；
- 项目当前未在本文件中声明统一开源许可证；对外发布前应由团队确认许可证和数据使用边界。

---

项目当前处于：**A、B、C 三模块完成；C 的调度/喘振已接入 B 的稳定接口，端到端闭环可用；D 待开发**。

---

## 知识库（阶段 K）

第五项平台能力：**知识库构建**。把设备/数据/模型/调度/安全/维护/验收知识整理为可检索知识卡，
支持自然语言问答；无依据问题明确拒答。

```bash
# 构建索引
python -m air_station.knowledge.indexer --build
# 命令行问答
python -m air_station.knowledge.service --question "负荷预测模型的输入和输出是什么"
# 测试
python -m pytest tests/knowledge -q
```

- 知识卡：`knowledge/`（10 类，12 份）；索引：`artifacts/knowledge/`
- 代码：`src/air_station/knowledge/`；配置：`configs/knowledge.yaml`
- 接口：`knowledge_json()` / `search_knowledge()`；MCP `knowledge_search`；
  API `POST /api/knowledge/{search,ask}`、`GET /api/knowledge/status`
- Agent：新增 `knowledge_query` 意图；实时问题仍走动态工具，组合请求先动态再知识解释。
- 指标真源：`artifacts/metrics/*.json`；来源变化 → 知识库标记 `stale`。
- 报告与验收：`analysis/reports/phaseK/`
