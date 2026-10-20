# B 组交付总入口（空压站多机协同智能调度）

本目录是 B 部分的统一交付入口；详细报告在上级 `analysis/reports/b/`。分支 `feat/b-int-a6`，**未推送**。

## 交付包索引
| 文件 | 内容 |
|---|---|
| `B_DELIVERY_README.md` | 本入口（成果、KPI、缺口、复现） |
| `ALGORITHMS_AND_API.md` | 算法清单 + 统一调度 API（`dispatch_api`）说明与样例 |
| `RUNBOOK.md` | 环境依赖、复现命令、长任务与进度 |
| `OPEN_ITEMS.md` | 待外部确认事项（A/C/D/官方） |
| `c_call_examples.json` | 三模式真实调用输出 + 异常/回退用例 |
| `load_ratio_all.json` | 三段×4 策略加载率 D1/D2/D3 |
| `../B5_UNIFIED_SUMMARY.md` | dev+valid+test 统一汇总（含 pooled） |
| `../B_KPI_FINAL_ACCEPTANCE.md` | KPI 最终验收表 |
| `../C_INTERFACE.md` | C 接口交付说明 |
| `../LOAD_RATIO_PRESSURE_STUDY.md` | 加载率/压力专项 |
| `../PRESSURE_DATA_AUDIT.md` / `../A6_TEST_DATA_AUDIT.md` / `../A6_SPLIT_VERIFICATION.md` / `../SAITI_KPI_VERIFICATION.md` | 数据来源与官方口径核查 |

## A. 算法（冻结 B1/B4 未改）
| 模块 | 入口 | 文件 |
|---|---|---|
| B1 人工规则基线 | `baseline.rules.decide` | `src/air_station/baseline/{rules,evaluator,pipeline}.py` |
| B2 设备功率模型 | `optimization.performance.estimate_power` | `src/air_station/optimization/performance.py`、`artifacts/models/power_model.json` |
| B3+B4 能效优先 | `optimization.service.optimize_dispatch` | `optimization/{service,optimizer,constraints}.py`、`safety/{boundaries,surge}.py` |
| bal 低负荷优先 | `unit_commitment.plan(UCConfig(balanced=True))` | `optimization/unit_commitment.py` |
| LRB-t10 加载率均衡 | `unit_commitment.plan(UCConfig(spread_balance=True, spread_tol=0.10))` | 同上 |
| 统一 API | `optimization.dispatch_api.plan_dispatch(mode,…)` | `optimization/dispatch_api.py` |

## B. 实验
- **三段回测**：`multikpi_dev_tol.json`（dev 12284）、`multikpi_valid_tol.json`（valid 1916）、`multikpi_test_0_3000.json`（test 2113）。
- **统一汇总**：`b5_unified_summary.json` + `../B5_UNIFIED_SUMMARY.md`（dev+valid 与 dev+valid+test 两总表）。
- **test 预测**：`artifacts/handoff_b_test/forecast_test_dispatch_calibrated.parquet`（2113 步，15/30/45/60/75）。
- **Pareto**：B3(能效) ↔ bal(低负荷) ↔ LRB-t10(加载率均衡)（见汇总 §3）。
- **回退/服务可比**：`fallback_audit.json`、`bal_sec_calibers.json`、`kpi_supply_service.json`。

## C. 工程验证
- **测试**：`tests/optimization` 136 passed（含 `test_dispatch_api.py` 三模式、`test_unit_commitment.py` spread）。
- **时延**：B3 p50 33.9–41ms、bal p50 7–18ms、LRB-t10 p50 7.8–19ms；max ≤0.5s（`kpi_latency.json`）。
- **安全回退**：`dispatch_api._fallback` 统一探回 B1，`execution_permission=simulation_candidate_only`，无 PLC 写路径。
- **压力/喘振可验证范围**：见 `PRESSURE_DATA_AUDIT.md`、`kpi_surge_events.json`（代理事件、无真实标签、1min 分辨率）。

## D. 集成交付（给 C）
- `C_INTERFACE.md`；`/workspace/analysis/reports/b/delivery/c_call_examples.json`（三模式调用样例与异常用例）；`RUNBOOK.md`（依赖与命令）。
- 模式：`b3` / `balanced` / `load_balanced`；**仅仿真候选，不含控制下发**。

## E. 待外部确认
见 `OPEN_ITEMS.md`（官方能效公式与基线、加载率定义、压力带/数据、CAL/AS 参数、真实喘振标签、C 端到端时延）。

## 状态分类（诚实边界）
- **已实现且经真实回测**：B1/B3/bal/LRB-t10 的 dev/valid/test 逐步状态连续调度；SEC/节电/低负荷/加载率/回退/时延。
- **内部工程假设下验证**：压力仿真、喘振代理分级。
- **当前数据不足、无法正式评价**：压力合格率 99.5%、压力波动下降 30%、喘振真实准确率/30s、官方能效得分。
- **需 C 集成后才能验证**：端到端 Agent 时延 ≤60s、PLC 指令链路。
