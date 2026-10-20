# B1 传统人工调度基线报告

- 阶段：B1（传统人工调度基线）
- 负责人：成员 B
- 分支：`feat/b-optimization`
- 日期：2026-10-18
- 依据任务书：《B成员人工基线调度优化与喘振预警详细任务书》V1.1 第 5 节
- 方案文档：`analysis/reports/b/B1_implementation_plan.md`
- 结论口径：本报告数值均为实机运行结果；未冻结参数以工程/仿真配置开发验证，未宣称可用于真实设备控制

---

## 1. 已实现文件清单

### 1.1 源码

| 文件 | 说明 |
|---|---|
| `src/air_station/optimization/contracts.py` | 公共契约（B1 最小子集，供 B3 复用）：StationSnapshot / ForecastResult / HealthAssessment / DispatchConstraints / DispatchResult 等，含 JSON 安全工具与状态/原因码 |
| `src/air_station/optimization/__init__.py` | 优化子包入口（仅导出契约） |
| `src/air_station/baseline/__init__.py` | 基线包入口 |
| `src/air_station/baseline/rules.py` | 规则引擎 `decide()`：压力迟滞、启停优先级、锁定、容量/备用、健康处理、结构化返回 |
| `src/air_station/baseline/evaluator.py` | 指标：`observed_metrics()`（历史实测轨迹）、`baseline_metrics()`（规则基线）、`count_switches()`、`compare_heads_up()` |
| `src/air_station/baseline/pipeline.py` | 历史回放：`snapshot_from_frames()`、`load_frozen_forecasts()`、`run_baseline_from_frames()`、`run_baseline()` |

### 1.2 配置

| 文件 | 说明 |
|---|---|
| `configs/baseline.yaml` | 全部阈值/优先级/来源标记外部化（时间、需求、备用、压力、容量、优先级、启停、健康、功率估算、加载率、物理约束、评估剔除） |

### 1.3 测试

| 文件 | 说明 |
|---|---|
| `tests/baseline/__init__.py` `conftest.py` `_helpers.py` | 测试包与构造器 |
| `tests/baseline/test_rules.py` | T01~T17、T20 |
| `tests/baseline/test_config.py` | T18（含配置缺失异常） |
| `tests/baseline/test_evaluator.py` | T19（含毛刺合并） |
| `tests/baseline/test_pipeline.py` | 时间边界与回放确定性 |

**未改动** A 的 `data/`、`artifacts/`、`configs/` 既有文件与源码；未修改 main；未推送。

## 2. 人工调度规则与关键参数

### 2.1 规则流程（`rules.decide`）

1. **设备合法性**：真实设备仅 4#、5#；出现 3# 或缺失 4#/5# → `INVALID_INPUT`。
2. **压力可用性**：压力缺失 → `INSUFFICIENT_DATA`。
3. **预测状态**：过期（晚于快照或超过 `max_forecast_age_minutes`）→ `STALE_FORECAST`；缺失 → 守恒保持 `FORECAST_MISSING_HOLD`（不新增启停）。
4. **需求与备用硬约束**：`required_flow = 预测指定时域 upper_bound`；`need = required + 额外备用`；`need > 全部有效容量` → `INFEASIBLE_CAPACITY`（无动作）；备用为 N-1 时另做单机失效校验 → `RESERVE_VIOLATION`。
5. **容量驱动**：`need` 超过当前运行容量时按启停优先级启动机组（保护供气，属硬约束）。
6. **压力驱动**：低于下限连续确认后启动调节机；高于上限连续确认后按停机优先级停机；每次至多切换一台。低于安全硬下限 `hard_lo` 立即响应。
7. **锁定与预算**：最小运行/停机时间未到 → `MIN_RUN_LOCK`/`MIN_STOP_LOCK` 延期；`max_switches_per_hour` 仅约束压力驱动的可选切换。
8. **健康处理**：`observe` 模式仅记录建议；`derate` 模式按系数降上限；`严重` 仅输出建议停机 `HEALTH_STOP_REVIEW`，**不自动停机**。
9. **流量分配**：基载机 4# 优先到高加载档，调节机 5# 吸收剩余；分配结果满足 `Σ各机流量 = total_flow_m3_min`。
10. **结构化返回**：逐机动作、原因码、约束检查（demand/capacity/reserve/pressure/health/switching）、`validation_mode`、`source_flags`、`action_sequence` 与中文解释。

### 2.2 关键参数（均来自 `configs/baseline.yaml`）

| 参数 | 值 | 来源 |
|---|---|---|
| 需求口径 `demand.basis` / `horizon_minutes` | `upper_bound` / 60 | pending（CAL-02） |
| 备用 `reserve.mode/value/unit` | `extra` / 0.05 / percent | pending（CAL-03） |
| 压力带 `pressure.band_mpa` | [0.55, 0.66] | pending（CAL-05） |
| 压力硬下限 `hard_lo_mpa` | 0.50 | inferred（AS-06） |
| 连续确认 `confirm_minutes` / 迟滞 | 3 / 0.02 | engineering |
| 启停优先级 | start [5#,4#]、stop [4#,5#] | inferred |
| 最小运行/停机 `min_run/stop_minutes` | 15 / 15 | engineering（AS-24） |
| 最大切换 `max_switches_per_hour` | 2 | engineering（CAL-06） |
| 额定容量 `rated_capacity_m3min` | 268.9 | real（AS-17） |
| 有效上限 `validated_operating_max_m3min` | 268.9（不主动超额） | engineering（AS-18） |
| 最低稳定流量 `validated_min_m3min` | 120 | engineering（AS-02） |
| 实测瞬时最大 `observed_max_m3min` | 299.7（仅参考） | real |
| 健康 `mode/status/derate_map` | observe / pending / 全 1.0 | pending（CAL-06/AS-19） |
| 功率估算 `power_estimate.mode` | none（B1 无性能模型） | — |

**数据来源区分**：额定容量为 `real`；有效上限/最低流量/启停时长为 `engineering`；备用/压力带/健康系数为 `pending`。以上非 `real/inferred` 项使 `validation_mode` 默认为 `simulation`。

## 3. 典型案例（实机输出）

| 案例 | 输入 | 输出 |
|---|---|---|
| 正常中等负荷 | 压力 0.60，双机运行，需求上界 480 | `success`，供气 480，4# 268.9 + 5# 211.1 m3/min |
| 低负荷/高压 | 压力 0.67，双机运行，需求 200 | `success`，停机 4#，5# 供 200，原因 `PRESSURE_HIGH_STOP` |
| 压力偏低启动 | 压力 0.53 连续 3 min，4# 运行、5# 停机，需求 200 | `success`，启动 5#，原因 `PRESSURE_LOW_START` |
| 容量不足 | 需求上界 560（+备用 > 总额定 537.8） | `INFEASIBLE`，`INFEASIBLE_CAPACITY`，无动作 |
| 压力缺失 | 母管压力 NaN | `INSUFFICIENT_DATA`，无动作 |
| 预测过期 | 预测早于快照 24h | `INSUFFICIENT_DATA` + `STALE_FORECAST`，不调度 |
| 健康预警限载（derate） | 4# 预警，系数 0.85 | 4# 上限降至约 228.6，原因 `HEALTH_DERATE` |
| 健康严重 | 4# 严重 | 不停机，原因 `HEALTH_STOP_REVIEW` |

**真实数据回放（2026-09-01，96 步，步长 15 min）**：

- 默认配置（备用 extra 5% + 需求上界）：可行率 **0.6875**，30 步 `INFEASIBLE_CAPACITY`；
- 备用设为 `none`：可行率 **1.0**，0 启停，53 步 `HOLD`、43 步 `ADJUST_LOAD`；
- 当日历史实测（observed_operation）：电耗约 62,800.8 kWh，供气约 700,939.5 m3，SEC ≈ 0.0896 kWh/m3，压力合格率（带 0.55~0.66）≈ 0.5389。

> 说明：默认配置下高可行失败率来自「预测上界 + 5% 额外备用」两处待确认口径叠加，属真实参数依赖，非代码缺陷。压力合格率对压力带取值高度敏感。

## 4. 测试实际结果

```
python3 -m pytest tests/baseline -q
24 passed in 0.30s
```

T01~T20 映射与结果：

| 编号 | 测试函数 | 结果 |
|---|---|---|
| T01 | test_t01_pressure_low_starts_machine | PASS |
| T02 | test_t02_pressure_high_stops_machine | PASS |
| T03 | test_t03_no_switch_inside_band | PASS |
| T04 | test_t04_hysteresis_no_reverse | PASS |
| T05 | test_t05_confirm_minutes_debounce | PASS |
| T06 | test_t06_min_run_lock_defers_stop | PASS |
| T07 | test_t07_min_stop_lock_blocks_start | PASS |
| T08 | test_t08_infeasible_capacity | PASS |
| T09 | test_t09_reserve_prevents_stop | PASS |
| T10 | test_t10_health_derate_reduces_limit | PASS |
| T11 | test_t11_health_critical_no_auto_stop | PASS |
| T12 | test_t12_missing_pressure_insufficient | PASS |
| T13 | test_t13_stale_forecast_rejected | PASS |
| T14 | test_t14_load_allocation_sums | PASS |
| T15 | test_t15_off_machine_zero | PASS |
| T16 | test_t16_deterministic_repeat | PASS |
| T17 | test_t17_json_serializable_no_nan | PASS |
| T18 | test_t18_config_sources_traceable、test_t18_missing_config_raises | PASS |
| T19 | test_t19_observed_vs_rule_baseline_named、test_t19_switch_count_merges_glitches | PASS |
| T20 | test_t20_only_devices_4_5 | PASS |
| 附加 | test_snapshot_uses_past_only、test_run_baseline_from_frames_deterministic | PASS |

覆盖要求确认：同输入确定性（T16、pipeline）、停机机流量/功率为 0（T15）、无 NaN/Infinity（T17）、健康不触发自动停机（T11）、过渡不忽略容量（T08/T09）、配置来源可追溯（T18）。

## 5. A 原有测试是否仍通过

```
python3 -m pytest tests/data tests/models tests/baseline -q
71 passed in 93.09s
```

- A 模块：47 passed（与基线一致）；
- B1 新增：24 passed；
- 跳过：0；
- 未修改 A 的任何代码或数据。

## 6. 尚未冻结的工程参数及其影响

| 参数 | 状态 | 影响 |
|---|---|---|
| 需求口径（上界/分位数） | 待确认（CAL-02） | 上界使需求偏高，直接影响可行率 |
| 备用定义与值 | 待确认（CAL-03） | extra 5% 使部分时段判定不可行；N-1 在双机下更严格 |
| 压力合格带 | 待确认（CAL-05） | 决定压力合格率与启停触发频率 |
| 最小运行/停机、最大切换次数 | 工程假设（CAL-06） | 影响切换次数与迟滞行为 |
| 健康限载系数 | 待确认（AS-19） | derate 模式下决定有效上限 |
| 最低稳定流量 | 工程假设（AS-02） | 影响低流量分配下界 |
| 有效运行上限 | 工程假设（AS-18） | 是否允许超过铭牌由厂家核验决定 |
| 时区 | 待确认（CAL-01） | 影响 `as_of` 对齐 |

以上参数未冻结前，所有相关输出携带 `source_flags` 且 `validation_mode=simulation`。

## 7. B1 是否满足 V1.1 验收标准

| V1.1 §5 验收项 | 结论 | 证据 |
|---|---|---|
| 同一输入重复得到同一方案 | 满足 | T16、pipeline 确定性测试 |
| 阈值可追溯 | 满足 | `configs/baseline.yaml` 全量 source + T18 |
| 基线与优化使用同一需求/备用/压力/安全约束与模型条件 | 满足 | 共用 `contracts.py` 契约与 `DispatchConstraints` |
| 规则实现并覆盖阈值附近/启停锁定 | 满足 | T01~T09 |
| ≥10 个用例 | 满足 | 20 用例（24 测试函数） |
| 缺数据/不可行结构化返回 | 满足 | T08、T12、T13 |
| 不生成未验证动作 | 满足 | 不可行时 `device_actions=[]` |

## 8. 需与 C 确认的字段与问题（进入 B2 前）

1. `DispatchResult` / `SurgeAssessment` 字段名与层级以 C 公共契约为准；本阶段 `DispatchResult` 为最小子集，`SurgeAssessment` 未实现（属 B4）。
2. 状态值集合：`success / INFEASIBLE / INSUFFICIENT_DATA / INVALID_INPUT / TIMEOUT` 是否与 C 一致。
3. 原因码命名（`STALE_FORECAST`、`INFEASIBLE_CAPACITY`、`SURGE_BOUNDARY_UNKNOWN` 等）需 C 确认。
4. `validation_mode` 取值约定（B1 使用 `simulation` / `rule_baseline`）。
5. `health` 输入的对象形态（`HealthAssessment`）与 A `health_json()` 的映射口径。
6. 基线对外函数命名（B1 提供 `rules.decide`；是否需包装为与优化器同形的 C 入口）。

## 9. 进入 B2 前仍需解决的问题

1. **性能模型缺失**：`estimated_power_kw` 当前为 `null`，B2 需建立功率-流量-压力模型并给出有效区间与误差。
2. **压力模型缺失**：`pressure_setpoint_mpa` 仅为建议值；B2 需提供母管压力近似与不确定区间。
3. **契约抽取**：B1 契约位于 `optimization/contracts.py`，B3 开始时确认是否抽为共享位置。
4. **回放效率**：`snapshot_from_frames` 逐步过滤整表，整窗回测偏慢，B5 前需改为基于 `searchsorted` 的增量索引。
5. **待确认口径**：CAL-01~06 与 CQ-01~08 未冻结，影响可行率与合格率结论。
6. **历史健康限载**：缺逐时刻健康评分，历史健康场景只能标 `simulation`。
7. **功率估算口径**：`power_estimate.mode=constant` 为占位，B2 落地后移除。

## 10. 复现命令

```bash
# B1 单测
python3 -m pytest tests/baseline -q

# 全量回归 (A + B1)
python3 -m pytest tests/data tests/models tests/baseline -q

# 真实数据一天回放
python3 -c "from air_station.baseline import pipeline, evaluator; \
r = pipeline.run_baseline(start='2026-09-01 00:00', end='2026-09-01 23:59', step_minutes=15); \
print(evaluator.baseline_metrics(r))"
```
