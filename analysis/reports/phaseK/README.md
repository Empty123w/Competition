# 阶段 K 知识库：来源、架构、运行与限制

第五项平台能力：**知识库构建**。把仓库中已有的设备、数据、模型、调度、安全、维护与验收知识整理为可检索内容，
支持自然语言问答，并对无依据问题**明确拒答**。

## 1. 知识来源（清单）

| 类别 | 主要来源 |
|------|----------|
| 设备档案 | `data/设备档案.json`、`data/指标释义.json` |
| 数据与指标 | `configs/data_quality.yaml`、`data/processed/*manifest.json` |
| 预测模型 | `configs/forecasting.yaml`、`configs/rolling_forecast.yaml`、`src/air_station/models/forecasting/service.py` |
| 健康与维护 | `configs/health_thresholds.yaml`、`configs/rul.yaml`、`configs/maintenance_policy.yaml`、`src/air_station/models/{health,rul}/` |
| 调度优化 | `configs/optimization.yaml`、`src/air_station/optimization/optimizer.py` |
| 喘振与安全 | `configs/safety.yaml`、`src/air_station/safety/surge.py` |
| 运维知识 | `data/processed/events.parquet`、`analysis/reports/phaseA5/health_report.md` |
| 系统接口 | `src/air_station/mcp_servers/tools.py`、`src/air_station/api/app.py`、`analysis/reports/A_to_C_服务接口.md` |
| 赛题验收 | `analysis/reports/团队进度总览.md`、`artifacts/metrics/*.json` |
| 常见问题 | `analysis/reports/HANDOFF.md` |

优先级：当前代码/冻结配置 > 测试 > 指标 JSON > 最新阶段报告 > 历史报告。

## 2. 架构

```
knowledge/**.md  --loader-->  --chunker-->  --indexer(TF-IDF char 2-4gram)-->  artifacts/knowledge/
                                                                            ├─ chunks.json
                                                                            ├─ vectorizer.pkl
                                                                            ├─ matrix.npz
                                                                            └─ knowledge_manifest.json
service.knowledge_json()/search_knowledge()  <-retriever(余弦)--
   ├─ MCP: tools.knowledge_search
   ├─ API: /api/knowledge/{search,ask,status}
   └─ Agent: intent=knowledge_query / 组合请求
```

- 指标真源：知识卡只记 `metric_refs`（路径+字段），构建时读取当前 JSON 数值生成"当前指标值"块；
  `knowledge_manifest.json` 记录来源文件哈希，**来源变化即标记 `kb_status=stale`**，回答会加过期提示。

## 3. 运行命令

```bash
python -m air_station.knowledge.indexer --build
python -m air_station.knowledge.service --question "负荷预测模型的输入和输出是什么"
python -m pytest tests/knowledge -q
python3 analysis/scripts/tune_knowledge.py       # 阈值网格标定
python3 analysis/scripts/kb_acceptance.py        # 验收指标
```

## 4. 阈值标定

`min_score` 由标准问题集（`tests/fixtures/knowledge_questions.json`，30 题）网格标定；
选择满足 `Top-3≥0.85` 且 `拒答≥0.90` 的**最大**阈值（避免低阈值大量误召）。
网格与选择依据见 `threshold_tuning.json`；最终 `min_score=0.11`。

## 5. 验收结果

见 `knowledge_acceptance.json`：文档 12 份 / 10 类 / 72 块；Top-3 命中 88.46%；
成功回答引用覆盖 100%；拒答率 100%；JSON 可序列化 100%；检索 P95 < 0.1s。

## 6. 能力边界

- 知识库只回答**方法与口径**类问题；当前实时值（健康分/预测/调度/喘振/控制）继续走动态工具。
- 仓库无依据的问题（供应商手册、外部系统）**明确拒答**。
- 模拟、混合仿真、离线回放与真实数据在知识卡与响应中显式区分。
