# B 组完整技术说明书

> 读者假设：未参与开发。本手册从基础概念讲起，给出公式、代码路径、函数名与数值例子。凡无法从源码/数据确认的，均标注 **（待确认）**。

## 0. 全局地图
```
configs/{baseline,optimization,safety}.yaml        参数与约束
src/air_station/
  baseline/  rules.py evaluator.py pipeline.py       B1 人工规则基线 + 数据读取
  optimization/
    performance.py  pressure.py                      B2 功率/压力模型
    optimizer.py service.py constraints.py contracts.py  B3/B4 求解与契约
    unit_commitment.py                               bal / LRB 分配
    selective_balanced.py energy_aware_balanced.py   研究用选择性均衡
    fallback.py                                      安全回退(B1)
    dispatch_api.py b_api.py                         B6 面向 C 的统一接口
  safety/ boundaries.py surge.py                     B4 喘振代理与边界
  simulation/ closed_loop.py                         B7 闭环压力仿真
analysis/scripts/*.py                                回测/实验/场景脚本
analysis/reports/b/                                  结果与报告
```

## 1. 基础概念
- **流量 Q（m³/min）**：空压机单位时间输出的压缩空气体积。
- **功率 P（kW）**：机组有功功率，来自实测或 B2 模型估计。
- **母管压力 p（MPa，表压）**：管网供气压力；本数据实测范围约 0–0.71。
- **加载率 λ**：`λ = 流量 / 额定容量(268.9)`；λ<0.5 记"低负荷/大马拉小车"（阈值冻结）。
- **SEC 单位气量电耗**：`SEC = ΣE / ΣV = Σ(P·Δt_h) / Σ(Q·Δt_min)`，单位 kWh/m³；越小越省电。
- **人工规则调度**：按固定阈值/优先级（压力高低、容量）启停与加载，无前瞻、无多组合寻优。
- **多机组合优化**：在"开哪几台 + 每台多少流量"的离散×连续空间中，满足约束并最小化代价。
- **硬约束 vs 软目标**：硬约束=容量/备用/最低稳定流量/最短开停/压力安全/喘振，违规则不可行；软目标=能耗/低负荷/切换次数，用于择优。

## 2. B1 人工规则基线（`baseline/rules.py`）
- 入口：`B1.decide(snapshot, forecast, health, constraints, config, state) -> DispatchResult`。
- 逻辑（工程规则，`configs/baseline.yaml`）：
  1. 需求取预测**上界** `basis: upper_bound`；
  2. 按 `start_priority=["5#","4#"]` 保证容量 ≥ 需求+备用；压力低于 `hard_lo` 则强制加机；
  3. `load_allocation`: 4# 基载、5# 调节；
  4. 单调逻辑与去抖（`confirm_minutes`、`hysteresis_mpa`）。
- 角色：**公平对照基线**；优化失败时作为**安全回退**（`fallback.rule_fallback`）。
- 备注：B1 默认**无功率模型**（`power_estimate.mode: none`），故 B5 用 B2 模型为两者统一计功耗。

## 3. B2 机组性能与管网模型（`optimization/performance.py` / `pressure.py`）
### 3.1 功率模型
- 训练入口：`performance.train_performance()` → `artifacts/models/power_model.json`（`model_version=b2-power-v0.1`）。
- 拟合样本：运行且流量∈[120, 268.9]（`fit_flow_lo/hi_m3min`）。
- 候选：`linear / poly2 / piecewise / multivar`，按 **MAE** 选优；特征含 `flow_m3_min, discharge_pressure_mpa, igv_percent, ambient_temp_c`。
- 估计入口：`PERF.estimate_power(device_id, flow, *, discharge_pressure_mpa, igv_percent, ambient_temp_c, model_path)`；
  - 返回 `estimated_power_kw`、`specific_power_kw_per_m3min`、`extrapolated`、`validation_mode`；
  - `flow=0` → 功率 0；非有限/负 → `INVALID_INPUT`；缺模型 → `MODEL_UNAVAILABLE`。
- 数值例子（4#）：Q=120→920.3 kW（SEC 0.1278），Q=240→1288.3 kW（SEC 0.0895）。**SEC 随流量下降**，这是"大马拉小车"不经济的根因。
- 局限：无厂家曲线，**工程近似**（`validation_mode=simulation`）。

### 3.2 压力模型
- 入口：`pressure.train_pressure()`、`predict_model(spec, df)`；候选 `persistence / linear_dynamics`，按 MAE 选优。
- 结论：母管压力近似**随机游走/被调控**，`corr(p, 总流量)≈−0.42`；一步预测**不优于持续性基线** → **反事实预测能力不足**（见压力报告）。
- 配置 `reference_band_mpa: [0.55,0.66]` 为**待确认示例**（CAL-05），非厂家安全标准。

## 4. B3 多机优化调度（`optimizer.py` + `service.py` + `constraints.py`）
### 4.1 入口与流程
`service.optimize_dispatch(snapshot, forecast, health, constraints, *, policy_version, rul, state, config_path, power_model_path, pressure_model_path, baseline_config_path)`：
1. **输入合法性**：设备集合必须等于 {4#,5#}，否则 `INVALID_INPUT`；
2. **模型可用性**：`power_model.json` 不存在 → `INSUFFICIENT_DATA`(`MODEL_UNAVAILABLE`)；
3. **预测状态**：过期 → `STALE_FORECAST`；缺失 → 保持策略；
4. 需求 `required = forecast.demand_value(demand_basis)`；
5. `optimizer.search_combinations(...)`：枚举 4 种组合（{}、{4#}、{5#}、{4#,5#}），组合内做**连续负荷分配**；目标见 §4.2；
6. 超时保护：`timeout_seconds`（默认 30s）→ `TIMEOUT`；
7. 过渡可行性 `CONS.transition_feasibility(...)`；
8. 压力反事实（标记 `PRESSURE_COUNTERFACTUAL_UNSUPPORTED`）；
9. 组装 `DispatchResult`（动作/约束检查/可解释字段）→ `_attach_surge` 附 B4。
- 失败/异常均走 `_with_fallback`：返回 B1 回退候选，`is_fallback=True`，不伪装成功。

### 4.2 目标函数（软目标）
`total_objective = w_power·(功率归一) + w_low·低负荷惩罚 + w_switch·切换次数`（`configs/optimization.yaml: dispatch.weights`，权值可配置）。
等价近似：**在硬约束可行域内，优先满足需求与备用，其次最小化能耗，再减少低负荷步与切换**。

### 4.3 硬约束（`constraints.py`）
- `build_capabilities`：逐机可用性（质量有效 & derate > 0）、`min_flow=120`、`max_flow ≤ 268.9`。
- 容量+备用：`need = required + reserve.extra_m3_min(required)`，`extra` 模式 5% → 单机可用阈值 268.9/1.05 = **256.1**。
- `transition_feasibility`：开关机过渡过程中供气不得中断（`supply_ok`）。
- 最短开/停机：`min_run_minutes/min_stop_minutes=15`、`max_switches_per_hour=2`。
- 加载率上限：`load_ratio.target/tolerance = null` → **尚无冻结上限**（CAL-04 待确认）。

### 4.4 组合内负荷分配（`unit_commitment.best_allocation`）
- 双机时在 `tot∈[required, 上限和]` 与分配比例上枚举，取**最小功率**满足需求；
- 受 `min/max` 与**爬坡**限制（`RAMP_STEP`: 4#=77、5#=287 m³/min per 15min，数据 p99 标定）；
- **bal**：当 `required ≥ 0.5·额定·台数` 时强制每台 ≥134.45（消除可再平衡低负荷）；
- **LRB-t10**（`spread_balance=True, spread_tol=0.10`）：双机时限制 `|f4−f5| ≤ 0.10·268.9`，容差内仍取最小功率；单机/不可行时自动退回最小功率（软约束回退）。

### 4.5 版本与回退
- 策略版本：`b3-optimizer-v0.1`、`balanced-uc-v0.1`、`load-balanced-lrb-v0.1`。
- 回退：`fallback.rule_fallback`（B1 + 复检），`execution_permission="simulation_candidate_only"`。

## 5. B4 喘振风险代理与安全（`safety/boundaries.py` / `surge.py`）
- 入口：`SG.assess_surge_risk(snapshot, candidate, *, boundary_version, config_path, boundary_path) -> SurgeAssessment`。
- 边界：`artifacts/models/surge_boundary.json`（历史运行点包络，`boundary_type=proxy`，**非厂家真实喘振线**）。
- 裕度：`margin = (planned_flow − boundary_flow(pressure)) / boundary_flow`；
  - `margin < 0` → **high**；`[0, warn_margin=0.10)` → **medium**；`≥ warn` → **low**；
  - 关键测量缺失/超域 → **unknown**（**不得默认 low**）。
- 阻断：`block_on_high=True`、`block_on_unknown=True` → `safety_blocked=True`（需人工复核，不自动执行）。
- 输出：逐机风险等级/裕度/边界来源/触发因素/建议；`validation_mode=simulation`。
- 触发因素：`below_historical_envelope`、`igv_low_percent`、BOV 状态等（`configs/safety.yaml: triggers`）。

## 6. B5 同工况回测（`analysis/scripts/multikpi_harness.py`、`b5_unified_summary.py`）
- 公平性：同一预测/同一设备可用性/同一压力约束/同一能效模型/同一备用/同一步长；**历史实际**单列。
- 时间网格：15 分钟整点发出，且具备 15/30/45/60 时域（缺则剔除，故 dev 12284 / valid 1916 / test 2113）。
- 逐策略**状态连续**推理（`PolicyState` 跨步继承开停/流量/锁定）。
- 评估口径：
  - `full_window`：全窗（各策略自有供气步不同步 → **不可直接比**，仅参考）；
  - `paired_exec`：所有策略 `status=success & exec=True`（可比）；
  - `service_equiv`：再要求各策略 `flow ≥ obs−0.05`（**严格同供气**）。
- 指标：`SEC`、同供气节电率、大马拉小车机分钟、缺供/过量供气、加载率 D1/D2/D3、回退/阻断。
- 禁止：把少供气当节能；把不可行步算作节能；平均各段百分比。

## 7. 闭环压力仿真（`simulation/closed_loop.py`）
- 组成：独立模拟**用气需求** `demand_series`；储气等效 `K_VOL`(m³/MPa)+泄漏；压力动态
  `p_{k+1}=p_k+Δt((Q_sup−Q_dem−Q_blow)/K_VOL − LEAK(p−p_env))`；
  BOV 比例泄放 `blowoff_flow`；压力 **PI** 偏置需求目标；执行延迟/预测噪声/设备故障。
- 显式记账：`pressure_step_accounted` 记录**安全泄压**与**未满足缺口**，钳制在 [0.40,0.75]（合格带外）。
- 守恒：`净入流 − 储气变化 − 泄漏 − 泄压 + 未满足 = 0`（残差 ≈0）。
- 场景 normal/low/peak/step/fault；结果见实验报告。
- 参数均为**工程假设**，与真实 SCADA 严格区分。

## 8. B6 接口与 C 集成
- 稳定导出：`optimization/b_api.py` → `optimize_dispatch`、`optimize_dispatch_json`、`assess_surge_risk`、`plan_dispatch`、`API_VERSION="b-api-v1.0"`。
- 统一入口：`dispatch_api.plan_dispatch(mode, snapshot, forecast, health, constraints, *, state, forecast_seq, config_path, timeout_ms)`，`mode∈{b3,balanced,load_balanced}`。
- 输出字段、错误码、回退、权限见 `05_B_TO_C_HANDOFF.md`。

## 9. B7 两典型场景
- 脚本：`analysis/scripts/b7_scenarios.py`；数据：`analysis/reports/b/b7/`。
- 场景一（负荷下降）：真实开发段窗口自动选取（2026-07-02 10:45~07-03 01:30，需求 223.8→119.2），输出逐机流量/功率/加载率/低负荷与同工况节能。
- 场景二（设备异常）：4#/5# 受控 fixtures；`unit5_derate` 单机重分配；负例：容量不足、高喘振、过渡供气不足。

## 10. 一次完整调度的内部调用流程（伪代码）
```
plan_dispatch(mode) →
  1) 读取 config(optimization.yaml) + power_model
  2) mode=="b3" → optimize_dispatch(...)：
       build_capabilities → search_combinations(枚举+分配+目标) → transition_feasibility
       → 压力反事实标记 → _attach_surge(assess_surge_risk) → DispatchResult
     mode in {balanced, load_balanced} → UC.plan(UCConfig(..., spread_balance?))
       → 返回 seq/alloc → 组装 DispatchResult → _attach_surge
  3) 失败/超时/不可行 → _fallback(B1 规则 + 复检) → is_fallback=True
  4) to_dict() → JSON（含 latency_ms / execution_permission）
```

## 11. 重要参数速查
| 参数 | 值 | 来源 |
|---|---|---|
| 额定容量 / 最低稳定流量 | 268.9 / 120 m³/min | 铭牌(real) / engineering(AS-02) |
| 低负荷阈值 | λ<0.5（134.45 m³/min） | 冻结 |
| 单机可用阈值（含 5% 备用） | 256.1 m³/min | 268.9/1.05 |
| 爬坡上限 4#/5# | 77 / 287 m³/min per 15min | 数据 p99 |
| 最短开/停机、最大切换 | 15/15 min、2 次/h | engineering |
| 压力带（示例） | [0.55,0.66] MPa | pending(CAL-05) |
| 喘振 warn 裕度 | 0.10 | configs/safety.yaml |

**（待确认）**：CAL-02 需求口径、CAL-03 备用、CAL-04 加载率目标、CAL-05 压力带、AS-02 最低稳定流量；官方能效公式与人工基线。
