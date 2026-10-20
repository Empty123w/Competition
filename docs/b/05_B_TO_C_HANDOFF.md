# B → C 正式交接（给 C 组开发人员）

> 状态：B 侧封装、测试、样例已完成；**MCP 与 PLC 联调未完成**（需 C 侧进行）。所有调度输出为仿真候选，无控制下发路径。

## 1. 模块入口与代码路径
| 用途 | 路径 | 入口 |
|---|---|---|
| 统一调度（推荐） | `src/air_station/optimization/dispatch_api.py` | `plan_dispatch(mode, ...)` |
| 稳定导出（契约） | `src/air_station/optimization/b_api.py` | `optimize_dispatch` / `assess_surge_risk` / `plan_dispatch`；`API_VERSION="b-api-v1.0"` |
| B3 能效优先 | `optimization/service.py` | `optimize_dispatch(snapshot, forecast, health, constraints, ...)` |
| 喘振评估 | `safety/surge.py` | `assess_surge_risk(snapshot, candidate, ...)` |

## 2. 依赖
`python>=3.11`；`pandas numpy pyyaml scikit-learn lightgbm joblib`。模型/配置：`artifacts/models/{power_model.json,pressure_model.json,surge_boundary.json}`、`configs/{optimization,baseline,safety}.yaml`。

## 3. 三种算法模式
| mode | 语义 | policy_version |
|---|---|---|
| `b3` | 能效优先 | `b3-optimizer-v0.1` |
| `balanced` | 低负荷优先 | `balanced-uc-v0.1` |
| `load_balanced` | 加载率均衡优先（主方案 LRB-t10） | `load-balanced-lrb-v0.1` |

## 4. Python 调用样例
```python
from air_station.optimization import b_api
from air_station.baseline import pipeline as PIPE, rules as B1
from air_station.optimization import contracts as C

snap = ...            # StationSnapshot(as_of, header_pressure_mpa, devices=[DeviceSnapshot,...])
fc   = ...            # ForecastResult(as_of, 15, predicted_flow, lower_bound, upper_bound)
con  = C.DispatchConstraints.from_config(B1.load_config("configs/baseline.yaml"))

out = b_api.plan_dispatch("load_balanced", snap, fc, [], con)      # 统一三模式
d   = b_api.optimize_dispatch_json(snap, fc, [], con)              # B3 + JSON
r   = b_api.optimize_dispatch(snap, fc, [], con)
sa  = b_api.assess_surge_risk(snap, r)                             # 喘振评估
```

## 5. JSON 调用（异常/正常样例）
见 `analysis/reports/b/delivery/c_call_examples.json`：
- 正常：`status="success"`、`device_actions`、`estimated_power_kw`、`surge_status`、`latency_ms`；
- 非法 mode → 抛 `ValueError`；缺失预测 → `status="INSUFFICIENT_DATA", is_fallback=true`；`timeout_ms<=0`（**三模式一致生效**）→ `status="timeout", is_fallback=true, reason_codes=["TIMEOUT"]`；容量超出 → `status="INFEASIBLE", reason_codes=["INFEASIBLE_CAPACITY"]`。

## 6. 输入输出字段（单位/类型）
**输入**：`snapshot.as_of: str(Asia/Shanghai, tz-naive)`；`device.flow_m3_min: float`（m³/min）；`discharge_pressure_mpa/header_pressure_mpa: float`(MPa)；`igv_percent/bov_closed_percent: float`(%)；`quality_valid: bool`。`forecast.predicted_flow/upper_bound: float`(m³/min)。
**输出**（节选）：`status:str`；`device_actions:[{device_id,action,target_flow_m3_min,load_ratio,running_after,estimated_power_kw}]`；`total_flow_m3_min/estimated_power_kw/estimated_energy_kwh: float`；`target_combination:[str]`；`transition_plan:[dict]`；`surge_status:str`；`surge_assessment:dict`；`safety_blocked:bool`；`reason_codes:[str]`；`constraints:[dict]`；`latency_ms:float`；`execution_permission:str`。

## 7. 错误码（`optimization/contracts.py`）
`INVALID_INPUT / STALE_FORECAST / INFEASIBLE_CAPACITY / INSUFFICIENT_DATA / MODEL_UNAVAILABLE / FORECAST_MISSING_HOLD / HEALTH_DERATE / HEALTH_STOP_REVIEW / RESERVE_VIOLATION / MIN_RUN_LOCK / MIN_STOP_LOCK / MAX_SWITCH_LOCK / TRANSITION_CAPACITY_GAP / TIMEOUT / SURGE_HIGH_BLOCKED / SURGE_UNKNOWN / SURGE_BOUNDARY_UNKNOWN / PRESSURE_COUNTERFACTUAL_UNSUPPORTED / RUL_SIMULATION_PENALTY_ON`。

## 8. 安全检查 / 超时 / 回退
- 喘振 high/unknown → `safety_blocked=true`，不自动执行。
- 任意失败/超时（`timeout_ms`，默认取 `optimization.yaml: dispatch.timeout_seconds`）→ **回退 B1 规则 + 复检**，`is_fallback=true`，回退候选在 `fallback_candidate`，**非 success**。
- 全程 `execution_permission="simulation_candidate_only"`。

## 8b. 状态判定规则（统一，勿按大小写自行匹配）
使用 `air_station.optimization.b_api` 的判定函数（不改返回字段/取值）：
`is_success`（成功且非回退）/ `is_fallback_result`（回退）/ `is_blocked`（`safety_blocked`）/ `is_infeasible`（不可行）/ `candidate_usable`（可展示的**仿真候选**，非执行许可）/ `needs_human_review`（失败/回退/阻断）/ `execution_allowed`（**恒 False**）。

- 状态取值现状（保留）：成功 `"success"`；失败 `"INFEASIBLE"/"INSUFFICIENT_DATA"/"INVALID_INPUT"`（大写在语义分支）与回退的 `"timeout"/"fallback"/"infeasible"`（小写）。`reason_codes` 为大写常量。
- **四类结果**：正常 / 容量不可行 / 安全回退 / 安全阻断；**后三类不得解释为可正常执行**；`safety_blocked=true` **禁止下发**。

## 9. A→B→C 数据流
`A 预测/健康(artifacts/handoff_b)` → `B 调度(b_api/plan_dispatch)` → `C Agent 编排 + MCP 工具` → `PLC/SCADA`（**由 C 负责，B 不实现**）。

## 10. 版本与权限
`API_VERSION=b-api-v1.0`；`POLICY_VERSIONS={b3,balanced,load_balanced}`；模型 `b2-power-v0.1`。**执行权限仅仿真候选**。

## 11. C 应执行的联调测试（必须由 C 完成）
1. 在 C 容器内 `import air_station.optimization.b_api` 并加载配置/模型；
2. 三种 mode 的样例求解（正常/异常/超时）；
3. MCP 包装后"预测→健康→调度→喘振→安全"端到端一次，确认 `safety_blocked` 能被 C 阻断；
4. 记录端到端时延（B 算法侧毫秒级；**端到端 ≤60s 需 C 实测**）；
5. 确认无任何 PLC 写指令被 B 侧发出。

**未完成项**：MCP/PLC 联调、C 端到端时延——**不得视为已通过**。
