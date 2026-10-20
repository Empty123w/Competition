# B3 多机协同优化调度报告

- 阶段：B3
- 负责人：成员 B
- 分支：`feat/b-optimization-aligned`
- 日期：2026-10-18
- 依据任务书：V1.1 第 7 节、第 3.3 节
- 结论口径：`success` 仅表示算法层仿真候选生成成功，**不等于现场安全放行**；所有能耗差异均为模型估计/仿真结果

---

## 1. 新增与修改文件

| 类型 | 文件 |
|---|---|
| 新增 | `src/air_station/optimization/constraints.py`（硬约束/组合可行性/切换过渡） |
| 新增 | `src/air_station/optimization/optimizer.py`（枚举、负荷分配、归一化目标、选优） |
| 新增 | `src/air_station/optimization/fallback.py`（规则回退候选 + 复检） |
| 新增 | `src/air_station/optimization/service.py`（`optimize_dispatch` 公共入口） |
| 新增 | `tests/optimization/test_constraints.py`、`test_optimizer.py`、`test_fallback.py`、`test_service.py`、`_b3_helpers.py` |
| 修改 | `configs/optimization.yaml`（新增 `dispatch:` 目标权重/模型/超时/RUL/来源标记） |
| 修改 | `src/air_station/optimization/contracts.py`（向后兼容扩展：原因码、`RulAssessment`、`DispatchResult` B3 字段） |
| 新增 | `analysis/reports/b/optimization_report.md`、`optimization_sample_feasible.json`、`optimization_sample_infeasible.json` |

未修改 A 的代码/模型；未改动 B1/B2 已有接口语义。

## 2. 四种机组组合与可行性筛选

- 真实设备仅 4#、5#；`constraints.COMBINATIONS` 枚举：`() / (4#) / (5#) / (4#,5#)`。
- 每个组合先判定硬约束（见 §4），不可行则记录原因码；可行组合再做连续负荷分配。
- 停机组合仅当需求≈0 且备用允许时可行；模拟 3# 不进入真实设备集合（测试 `test_only_devices_4_and_5`）。

## 3. 负荷分配与目标函数

**分配**（`optimizer.allocate_flow`）：每台先置于 `validated_min`，剩余需求按**边际功率**从低到高填充（边际由 B2 模型端点差分得到），箱约束 `[validated_min, validated_operating_max]`。确定性、可解释。

**目标**（分项归一化后加权，不同量纲不相加）：

```
total = w_power·(P/P_ref) + w_switch·(n_sw/N_ref) + w_health·H + w_rul·R
P_ref = 2500 kW(2×额定), N_ref = 2 次
H = 运行机组平均健康惩罚(正常0/关注0.2/预警0.5/严重1.0, 来自无监督风险)
R = 运行机组平均 max(0,(threshold-RUL)/threshold), 默认权重 0
```

默认权重：`power 1.0 / switch 0.05 / health 0.10 / rul 0.0`（`configs/optimization.yaml`）。所有分项与权重在 `objective_components` 中逐项报告。

**健康**：无监督风险仅作**软惩罚**，不是真实故障概率，不触发停机。
**RUL**：默认关闭；仅显式 `simulation` 时参与排序（§6）。

## 4. 硬约束与待确认参数

已实现硬约束（`constraints.py`）：

- 预测供气需求（`required = demand_basis` 值）；
- 备用：区分 `extra`（额外可用流量）与 `n_minus_one`（单机失效校验），两者不混用；
- 单机额定容量与 `validated_operating_max`；
- 加载率上下限 `[validated_min, validated_operating_max]`；
- 最短运行/停机（`PolicyState.last_switch_at`）；
- 允许启停次数（`max_switches_per_hour`）；
- 设备当前可用状态（quality_valid / derate 严重不可用）；
- 健康限载（derate 模式）；
- 切换过渡连续供气（逐步检查，§5）。

待确认参数（沿用 B0 登记，标 `pending/engineering`，非厂家正式限制）：
CAL-02 需求口径、CAL-03 备用定义、CAL-04 加载率口径、CAL-05 压力合格带；启停时长/次数为工程假设。未知安全参数不默认视为满足。

## 5. 切换序列

`constraints.transition_feasibility` 构造分步计划：**先启动替代容量，再降载/停机原机组**；每步记录 `capacity_after_m3min` 与 `supply_ok`。若中间步出现容量缺口 → `TRANSITION_CAPACITY_GAP`；锁定未到 → `MIN_RUN_LOCK/MIN_STOP_LOCK`。缺乏可靠动态模型，`transition_plan` 仅为**待验证建议序列**，`requires_field_review=True`。

## 6. 压力、喘振与执行许可

- 压力：调用 B2 `predict_pressure`（persistence）。**不宣称优化后压力安全**，统一标记 `PRESSURE_COUNTERFACTUAL_UNSUPPORTED`，压力影响待复核。
- 喘振：已接入 B4 代理评估（见 `surge_risk_report.md`）。`surge_status ∈ {low,medium,high,unknown}`，`high`/`unknown` 触发 `safety_blocked`，**不默认 low**；关闭 B4 时回退占位 `not_assessed_b4`。
- `execution_permission="simulation_candidate_only"`（永不为 authorized）；`simulation_feasible` 与现场许可分离；`requires_field_review=True`。

## 7. B1 / B3 同工况初步对比

场景：`2026-09-01` 全天，逐分钟 1440 步；B1 与 B3 使用**相同**需求（upper_bound）、备用（extra 5%）、压力、容量、启停约束与预测数据（冻结测试集 60 min）。

评估口径：时间范围 2026-09-01 00:00~23:59，步长 **1 分钟**，功率单位 **kW**，电量单位 **kWh**（`energy_kwh = Σ power_kw × 步长/60`）。仅统计 B1 与 B3 **共同可行**的 983 步（B1-only / B3-only 均为 0）。

| 指标 | B1 规则基线 | B3 优化器 |
|---|---|---|
| 共同可行步数 | 983 / 1440 | 983 / 1440 |
| 平均功率(kW) | 2,620.44 | 2,535.96 |
| 预计总电量(kWh) | **42,931.56** | **41,547.51** |
| 预计节电（模型估计） | — | **3.224%** |
| 启停次数 | 0 | 0 |
| 组合分布 | 双机 983 | 双机 983 |

- 完整指标：`analysis/reports/b/optimization_metrics.json`（含 `n_steps/step_minutes/common_feasible_steps/horizon_hours_common` 等）。
- **量纲修正记录**：初版报告把逐分钟 `power_kw` 直接求和却标记为 kWh（缺 `× Δt_h`），数值夸大 60 倍；已修正为 `Σ power_kw × (1/60)`。节电率为比值，不受步长缩放影响，**3.224% 仍成立**。
- 不可行步均为 `INFEASIBLE_CAPACITY`（457 步）：预测上界 + 5% 额外备用叠加，与 B1 一致，非代码缺陷。
- 节电来自负荷分配差异（相同供气任务下的模型估计），**非现场实测**。
- 单机场景（reserve=none，需求 upper=200，双机初始运行）：B1 估计 2039.7 kW vs B3 1165.6 kW（**约 42.9%**，模型估计）。**根因是策略差异而非约束差异**：两者约束完全相同（reserve=none，无启停锁定）；B1 规则“运行机组优先保持”在压力处于合格带（0.60）时不动作，保持双机各 120 m3/min（合计 240，超供）；B3 选择单机 4#@200 并停 5#。默认备用（extra 5%）不允许该切机，故 983 步对比中两者均为双机。
- 未修改 B1；差异纯来自算法。

## 8. RUL ON/OFF 仿真对照

测试 `test_rul_penalty_toggle_changes_selection`（reserve=none，需求 200，双机运行）：

| 配置 | 选中组合 | 说明 |
|---|---|---|
| RUL OFF（默认） | `4#` | 按功耗最优 |
| RUL ON（w_rul=5，4# 模拟 RUL=100h，5#=900h） | `5#` | 避开低寿命机 |

- 默认不开启；仅在 `rul.enabled=true` 且 `simulation` 场景参与排序；输出含 `RUL_SIMULATION_PENALTY_ON` 与 `validation_mode=simulation`。
- RUL 缺失时不补造；不触发停机；不代表真实维修结论；不将 2028 模拟时间映射到 2026 真实历史。

## 9. 计算耗时

- 环境：Python 3.11.2，pandas 3.0.6；样本量：2026-09-01 共 1440 次调用。
- `optimize_dispatch` 单次耗时：min 0.0131 s / median 0.0256 s / max 0.0722 s，**远低于 30 s 目标**。
- 超时保护：`dispatch.timeout_seconds` 超限返回 `TIMEOUT`（测试 `test_timeout`）。

## 10. 结构化状态与回退

状态：`success / INFEASIBLE / INSUFFICIENT_DATA / INVALID_INPUT / TIMEOUT`；
原因码覆盖 `MODEL_UNAVAILABLE / STALE_FORECAST / INFEASIBLE_CAPACITY / RESERVE_N1_INSUFFICIENT / TRANSITION_CAPACITY_GAP / SURGE_NOT_ASSESSED_B4 / PRESSURE_COUNTERFACTUAL_UNSUPPORTED / RUL_SIMULATION_PENALTY_ON` 等。

失败时返回经**重新检查**的 `fallback_candidate`（`candidate_type="rule_fallback"`，`is_fallback=True`，`execution_permission=simulation_candidate_only`），不伪装成优化成功。

## 11. 测试结果

```
python3 -m pytest tests/optimization -q -rs          -> 64 passed, 0 failed, 0 skipped
python3 -m pytest tests/data tests/models tests/baseline tests/optimization -q -rs -> 142 passed, 0 failed, 0 skipped
```

覆盖要求逐项：双机正常、单机满足、双机不足、备用阻止停机、最短运行/停机、健康限载/不可用、RUL 开关、超出模型范围、压力反事实、喘振待评估、切换供气、预测过期/缺失、超时、确定性、JSON、回退、来源与 simulation 标记。

## 12. V1.1 §7 验收对照

| 要求 | 结论 |
|---|---|
| 两机组每种组合覆盖 | 满足（`test_search_returns_four_results`） |
| 容量不足/高风险/健康限载不输出可执行方案 | 满足（`INFEASIBLE`、`health derate unavailable`） |
| 单次求解 ≤30s | 满足（median 0.025s） |
| 输出组合/流量/加载率/代价/原因/顺序 | 满足 |
| 区分仿真可行与现场可执行 | 满足（`simulation_feasible` / `execution_permission`） |
| 结构化错误与回退 | 满足 |

## 13. B4 喘振代理需要的接口与剩余风险

**B4 需提供** `assess_surge_risk(snapshot, candidate DispatchResult, *, boundary_version) -> SurgeAssessment`，逐机含：`risk_level(low/medium/high/unknown)`、`margin_value/margin_unit/margin_definition`、`boundary_source/boundary_version`、`trigger_factors[]`、`limit_suggestion`、`validation_mode`。

**B3 现状**：`surge_status="not_assessed_b4"`，占位字段；未来 B3 将消费 B4 结果并据此设置 `safety_blocked`。

**剩余风险**：
1. 压力反事实能力不足，优化后压力安全不可验证；
2. 4# 功率模型测试期约 +15 kW 系统偏差，可能影响候选排序与节能量级；
3. 默认备用策略（extra 5%）导致 31.7% 步不可行，且禁止单机切机，节电被保守化——需 CAL-03 冻结；
4. B4 未开发前，所有候选喘振风险未知；
5. 目标权重与健康/RUL 惩罚为工程假设，需 C/工艺共同冻结。

## 14. 需成员 C 确认的字段清单

1. `DispatchResult` 新增字段：`target_combination`、`estimated_energy_kwh`、`pressure_prediction`、`surge_status`、`transition_plan`、`baseline_comparison`、`execution_permission`、`simulation_feasible`、`requires_field_review`、`fallback_candidate`、`is_fallback`。
2. 新状态/原因码命名（§10）。
3. `optimize_dispatch` 签名新增关键字参数：`policy_version`（必填关键字）、`rul`、`state`、`config_path`、`power_model_path`、`pressure_model_path`、`baseline_config_path`（均有默认）。
4. `RulAssessment` 契约与 `rul_json()` 的映射（`RulAssessment.from_rul_json`）。
5. `execution_permission` 语义：C 必须独立安全校验与人工确认，不得据 `success` 直接下发。

## 15. 复现命令

```bash
python3 -m pytest tests/optimization -q -rs
python3 -m pytest tests/data tests/models tests/baseline tests/optimization -q -rs
```
