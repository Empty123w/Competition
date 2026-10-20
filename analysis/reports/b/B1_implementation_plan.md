# B1 人工调度基线文件级实施方案

- 阶段：B1（传统人工调度基线）
- 负责人：成员 B
- 分支：`feat/b-optimization`
- 日期：2026-10-18
- 依据任务书：《B成员人工基线调度优化与喘振预警详细任务书》V1.1 第 5 节
- 状态：方案待确认后编码；本文件为设计说明，不含实现代码

---

## 1. 目标与边界

### 1.1 目标

在真实历史数据上建立**可复算、可解释的传统人工规则调度基线**，作为 B3 优化器的公平对照。

### 1.2 边界（V1.1 §5）

- 基线是**对照算法**，不是逐条复制历史开停机动作；历史运行含人无法预知的未来工况，直接复制后验动作会破坏公平性（V1.1 §5 开头）。
- 基线输入与优化器**同一套** `snapshot/forecast/health/constraints`，只用规则、**不窥视未来真实负荷**（V1.1 §5.1）。
- 历史实际轨迹另命名为 `observed_operation`，与规则基线**并列**报告。
- 本阶段只交付 `baseline/`，不开发 B2~B7。
- 涉及压力带、启停时长、健康限载等未冻结参数，一律用**明确标记的工程/仿真配置**开发验证，不得宣称满足真实工业控制安全要求。

---

## 2. 文件结构（任务点 1）

```
src/air_station/baseline/
  __init__.py      # 导出 run_baseline / evaluate_observed / 契约类型
  contracts.py     # B1 本地输入/输出契约（B3 开始时抽取为共享契约，见 §9）
  rules.py         # 规则引擎：输出开停、各机加载、设定压力（V1.1 §5.2）
  evaluator.py     # 指标与动作原因：启停次数、压力越界、供气不足、能耗、不可行
  pipeline.py      # 历史回放/滚动调度主流程，供 evaluator 与外部调用
configs/
  baseline.yaml    # 规则、阈值、优先级、锁定时间、来源标记
tests/baseline/
  test_rules.py test_evaluator.py test_pipeline.py test_config.py
```

### 2.1 各文件职责

| 文件 | 职责 | 关键函数（建议） |
|---|---|---|
| `contracts.py` | 定义 `StationSnapshot`、`ForecastResult`、`HealthAssessment`、`DispatchConstraints`、`DeviceAction`、`DispatchResult`（B1 子集）及其校验 | `StationSnapshot.from_frame(...)`、`to_dict()` |
| `rules.py` | 纯规则决策：压力迟滞、启停优先级、最短运行/停机、加载分配、守恒规则；**无 I/O、无全局状态** | `decide(snapshot, forecast, health, constraints, config, policy_state) -> DispatchResult` |
| `evaluator.py` | 对一段回放结果计算指标并汇总动作原因 | `evaluate_baseline(result) -> dict`、`evaluate_observed(df, config) -> dict` |
| `pipeline.py` | 按 `as_of` 滚动：取当前可见数据 → 调 `rules.decide` → 记录；保证确定性 | `run_baseline(processed_dir, forecast_dir, config_path, window) -> list[DispatchResult]` |
| `baseline.yaml` | 全部阈值/优先级/来源标记外部化，代码不硬编码 | — |

> 说明：`rules.py` 只依赖传入对象与配置，便于单测与 B3 复用同一约束语义。

---

## 3. 输入契约与统一口径（任务点 3、7）

B1 输入与 B3 完全一致（V1.1 §5.1、§3.3）。字段与单位：

```python
StationSnapshot: as_of, devices:[{device_id, running, flow_m3_min, power_kw,
                  discharge_pressure_mpa, igv_percent, bov_closed_percent,
                  quality_valid}], header_pressure_mpa, data_source
ForecastResult:  as_of, horizon_minutes, predicted_flow, lower_bound, upper_bound, forecast_id
HealthAssessment: device_id, risk_level, health_score, suggestion, source
DispatchConstraints: pressure_band_mpa, reserve_def, reserve_value, load_ratio_target,
                  load_ratio_tol, min_run_minutes, min_stop_minutes,
                  max_switches_per_hour, validated_operating_max_m3min,
                  rated_capacity_m3min, surge_policy, health_derate_map
```

统一口径（V1.1 §3.2）：

- 设备编号内部统一为整数 `4/5`，对外保持 `"4#"/"5#"`；保留原值与来源。
- 时间：内部统一为带时区时间戳；`forecast` 的无时区 `time` 与 `health` 的 UTC `evaluation_time` 先归一，再比较（CAL-01）。
- `risk_level` 中文枚举映射为内部 `normal/attention/warning/critical`，保留原文。
- 未知/缺失一律拒绝默认为安全（缺数据 → 结构化错误，不得默认正常）。

---

## 4. 规则算法设计（任务点 3、4）

### 4.1 状态量

- `required_flow`：**鲁棒需求**，默认取 `forecast.upper_bound`（CAL-02，可配置为均值或分位数）。
- `reserve`：按冻结定义计算（CAL-03）；默认“额外可用流量” = `Σ可用容量 − required_flow`；若冻结为 N-1，则额外校验单机失效后仍满足需求。
- `available_capacity`：运行中/可启动设备的 `validated_operating_max_m3min` 之和。
- 每台机 `load_ratio = flow / rated_capacity`。

### 4.2 压力迟滞（避免 1 分钟扰动频繁切换）

- 设压力带 `[p_lo, p_hi]`（CAL-05，示例 0.55~0.66 MPa，待确认）。
- 触发增载/启机：`p < p_lo` 且**连续** `confirm_minutes` 满足（默认 3 分钟）。
- 触发减载/停机：`p > p_hi` 且连续 `confirm_minutes` 满足。
- 迟滞带：在 `[p_lo, p_hi]` 内保持现状；另设 `hysteresis_mpa` 使“回到范围内后”不立即反向动作。
- 去抖：单分钟越界不动作；只在连续确认后动作（对应 V1.1 §5.2“低负荷不因 1 分钟扰动频繁启停”）。

### 4.3 启停优先级

- `start_priority` / `stop_priority` 由配置给出，依据实测行为：5# 运行占比 97%（调节机）、4# 82%（基载机）。
- 启动：按 `start_priority` 选下一台可启动设备（当前停机、未在停机锁定、健康允许）。
- 停机：按 `stop_priority` 从可停设备中选择（当前运行、满足最小运行时长、不在减载过渡中）。
- 同型号不同效率时按**设备模型**选择，不强行平均（V1.1 §验收 B13）。

### 4.4 最短运行/停机时间（锁定）

- 每台机记录 `last_switch_at`；运行时长 < `min_run_minutes` 或停机时长 < `min_stop_minutes` 时**禁止切换**。
- 若需求要求停机但锁定未到：输出**延期建议**（`deferred_stop`），不强行停机（对应 B06）。

### 4.5 加载分配（规则版）

- 需求在已运行机组内分配：基载机（4#）优先到高加载档，调节机（5#）吸收剩余量。
- 保持实测的加载偏好：优先让运行机处于高效流量区（比功率随流量下降，V1.1 §6.2、B0 报告 §4.5）。
- 单机加载限制在 `[validated_min_m3min, validated_operating_max_m3min]`。

### 4.6 预测缺失时的守恒规则

- `forecast` 缺失/过期：不新增启停，仅依据当前压力做最小必要调整；输出 `reason_code=FORECAST_MISSING_HOLD`。
- 预测时间早于快照 `as_of`：返回 `STALE_FORECAST`，不调度（对应 B11）。
- 需求超过总可用容量：`INFEASIBLE_CAPACITY`，不输出动作（对应 B02）。

---

## 5. 异常、健康与不可行处理（任务点 5）

| 场景 | 处理 | reason_code |
|---|---|---|
| 关键数据缺失（压力/状态/质量无效） | 保守保持现状并标记；容量无法判定时拒绝求解 | `INVALID_INPUT` / `INSUFFICIENT_DATA` |
| 预测过期/时间不对齐 | 不调度 | `STALE_FORECAST` |
| 需求 > 总可用容量 | 无动作清单 | `INFEASIBLE_CAPACITY` |
| 健康 `预警` | 按 `health_derate_map` 降低该机上限（系数为工程假设） | `HEALTH_DERATE` |
| 健康 `严重` | 输出“建议停机 + 待人工确认”，**不自动停**，并复核他机容量 | `HEALTH_STOP_REVIEW` |
| 停机后他机容量不足 | 拒绝停机，保留机组 | `RESERVE_VIOLATION` |
| 求解异常/超时 | 明确状态，附 `fallback_candidate` 但需再检查 | `TIMEOUT` |

- 健康限载在缺少逐时刻评分时标 `simulation`，不得用最新 `health_json()` 回填历史（V1.1 §2.1）。
- 缺数据不得默认安全；`INFEASIBLE` 不伪造动作清单。

---

## 6. `configs/baseline.yaml` 参数清单与来源（任务点 2）

| 参数 | 建议初值 | 来源 | 关联 | 状态 |
|---|---|---|---|---|
| `pressure_band_mpa: [0.55, 0.66]` | 示例 | 待确认示例 | CAL-05 | 待确认 |
| `pressure_hard_lo_mpa: 0.50` | 0.50 | inferred（实测 p01≈0.48） | AS-06 | 待确认 |
| `confirm_minutes: 3` | 3 | engineering | — | 占位 |
| `hysteresis_mpa: 0.02` | 0.02 | engineering | — | 占位 |
| `start_priority: [5, 4]` | 5→4 | inferred（运行占比） | — | 可用 |
| `stop_priority: [4, 5]` | 4→5 | inferred | — | 可用 |
| `trim_device: 5` | 5 | inferred（IGV 调节） | AS-15 | 可用 |
| `min_run_minutes: 15` | 15 | engineering | AS-24 | 待确认 |
| `min_stop_minutes: 15` | 15 | engineering | AS-24 | 待确认 |
| `max_switches_per_hour: 2` | 2 | engineering | CAL-06 | 待确认 |
| `required_flow_basis: upper_bound` | 上界 | CAL-02 | CAL-02 | 待确认 |
| `reserve_mode: extra` / `value: 5%` | 额外 | CAL-03 / AS-11 | CAL-03 | 待确认 |
| `rated_capacity_m3min: 268.9` | 268.9 | real（档案） | AS-17 | 可用 |
| `validated_operating_max_m3min: 268.9` | 268.9（不主动超额） | real/保守 | AS-18 | 可用 |
| `validated_min_m3min: 120` | 120 | engineering | AS-02 | 待确认 |
| `health_derate_map` | 1.00/0.95/0.85/0.0 | engineering | AS-19 | 待确认 |
| `source_flags` | `{pressure_band: pending, min_run: engineering, health_derate: engineering}` | — | — | — |

- 所有阈值写入 YAML 并在报告与输出中携带 `source_flags`，代码不硬编码。

---

## 7. 指标口径（B5 复用，任务点 3）

| 指标 | 计算 |
|---|---|
| 总电耗 E | `Σ power_kw × Δt_h`，kWh |
| 供气量 V | `Σ flow_m3_min × Δt_min`，m3 |
| 单位气量电耗 SEC | `ΣE / ΣV`，kWh/m3 |
| 节电率 | `(E_baseline − E_optimized)/E_baseline`（须同供气任务） |
| 压力合格率 | 落在压力带内采样点占比（剔除 0 值掉点） |
| 超额运行率 | 运行态 `load_ratio>1` 占比 |
| 启停次数 | `running` 0/1 翻转次数（剔除 <5 min 遥测毛刺段） |
| 供气缺额 | `Σ max(required_flow − supplied_flow, 0)` |
| 运行台数分布 | 0/1/2 台占比 |

- 基线报告同时输出规则基线与 `observed_operation` 两列，不混淆（V1.1 §5.1、§9.1）。

---

## 8. pytest 测试设计（≥10，任务点 6）

| 编号 | 测试 | 构造 | 期望 |
|---|---|---|---|
| T01 | `test_pressure_low_starts_machine` | 压力连续低于下限 | 按 `start_priority` 启机，附 `reason_code` |
| T02 | `test_pressure_high_stops_machine` | 压力连续高于上限 | 按 `stop_priority` 停机 |
| T03 | `test_no_switch_inside_band` | 压力带内 | 不动作 |
| T04 | `test_hysteresis_no_reverse` | 越界后回到带内单点 | 不反向切换 |
| T05 | `test_confirm_minutes_debounce` | 单分钟越界 | 不动作（去抖） |
| T06 | `test_min_run_lock` | 停机请求但未达最小运行 | `deferred_stop`，不停机（B06） |
| T07 | `test_min_stop_lock` | 启动请求但未达最小停机 | 阻断启动 |
| T08 | `test_infeasible_capacity` | 需求 > 总可用容量 | `INFEASIBLE_CAPACITY`，无动作（B02） |
| T09 | `test_reserve_prevents_stop` | 停机后他机不满足备用 | `RESERVE_VIOLATION`，保留机组（B03） |
| T10 | `test_health_derate` | 一机 `预警` | 该机上限按系数下降，检查他机容量（B04） |
| T11 | `test_health_critical_no_auto_stop` | 一机 `严重` | 输出建议停机，不自停（B04） |
| T12 | `test_missing_pressure_holds` | 压力缺失 | 保守保持 + `INVALID_INPUT`/`INSUFFICIENT_DATA` |
| T13 | `test_stale_forecast_rejected` | 预测 `as_of` 早于快照 | `STALE_FORECAST`，不调度（B11） |
| T14 | `test_load_allocation_sums_to_required` | 中等需求 | `Σ各机流量 = 计划总量`（B6 契约） |
| T15 | `test_zero_flow_off_machine` | 停机机 | 流量与功率为 0，无比功率污染 |
| T16 | `test_deterministic_repeat` | 同输入两次 | 结果、原因、版本完全一致（B16） |
| T17 | `test_json_serializable_no_nan` | 任意成功结果 | `allow_nan=False` 通过 |
| T18 | `test_config_sources_present` | 加载配置 | 每个阈值带 `source`，无硬编码 |
| T19 | `test_observed_vs_rule_baseline_named` | 指标输出 | 两列命名分离，不混用 |
| T20 | `test_device_ids_only_4_5` | 输入含 3# | 拒绝，仅 4#/5# 生效（B18） |

- 阈值附近用例：对 `p_lo`、`p_hi`、`confirm_minutes`、`min_run/stop` 取边界值与 ±1 单位。
- 关键测试若因数据/模型缺失被 skip，须报告 skip 数，不得宣称已验收（V1.1 §13 末）。

---

## 9. B1 ↔ B3 一致性与契约演进（任务点 7）

- B1 与 B3 使用**同一** `snapshot/forecast/health/constraints` 与同一约束语义（需求、备用、压力、健康、启停）。
- `contracts.py` 先在 `baseline/` 内定义；B3 开始时抽取为共享契约（候选位置：`optimization/contracts.py` 或统一 `air_station/contracts/`），命名与字段以 **C 的公共契约为准**（V1.1 §3.3）。
- 基线输出与优化器输出同为 `DispatchResult` 结构，仅 `policy_version`/`validation_mode` 不同，确保 B5 可并列表比较。
- 基线中所有未冻结参数以 `source_flags` 标注，B3 复用同一配置读取，避免口径漂移。

---

## 10. 参数依赖与待确认（任务点 5、7）

B1 编码即依赖以下**待确认**参数（缺省值仅用于开发验证，带 `source` 标记）：

| 依赖 | 关联 | 缺省处理 |
|---|---|---|
| 压力合格带 | CAL-05 | 示例 0.55~0.66，标 `pending` |
| 供气需求口径 | CAL-02 | 用 `upper_bound` |
| 备用定义 | CAL-03 | 先用“额外流量 5%” |
| 最小运行/停机、最大启停次数 | CAL-06、AS-22~24 | 工程占位 15 min / 2 次每小时 |
| 健康限载系数 | AS-19 | 1.00/0.95/0.85/0.0 |
| 有效运行上限 | AS-18、B14 | 不主动超额定（268.9） |
| 时区 | CAL-01 | 暂按 Asia/Shanghai |

不依赖厂家喘振曲线，故 B1 不被 BL-01 阻塞。

---

## 11. 实施步骤与验收

1. 建 `baseline/` 包与 `contracts.py`、`configs/baseline.yaml`；
2. 实现 `rules.py`（纯函数）与 `evaluator.py`、`pipeline.py`；
3. 建 `tests/baseline/`，实现 ≥10（目标 20）用例；
4. 运行 `python3 -m pytest tests/baseline -q`，报告通过/失败/跳过数；
5. 在训练/验证段标定阈值，**冻结后**在最终评估段（2026-08-21~09-12）回测；
6. 产出 `analysis/reports/b/baseline_report.md`、`artifacts/metrics/baseline_metrics.json`。

验收（V1.1 §5 验收）：同一输入重复得到同一方案；阈值可追溯；基线与优化使用同一需求、备用、压力、安全约束与模型条件。

---

## 12. 待用户确认事项

1. 是否批准本 B1 文件与测试方案；
2. `contracts.py` 的放置位置（baseline 本地 vs 提前共享）；
3. CAL-01~06 中可先行决策的参数（压力带、备用、加载率口径、启停时长）；
4. 是否允许在 `baseline/` 输出中保留 `observed_operation` 并列基准。

确认后再开始编写 B1 代码；在此之前不修改 main、不推送、不开发 B2~B7。
