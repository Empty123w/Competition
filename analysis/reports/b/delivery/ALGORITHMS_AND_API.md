# 算法与统一调度 API（C 集成）

代码：`src/air_station/optimization/dispatch_api.py`
入口：`plan_dispatch(mode, snapshot, forecast, health, constraints, *, state=None, forecast_seq=None, config_path=..., timeout_ms=None) -> dict`

## 1. 三种模式
| mode | 语义 | 实现 | policy_version |
|---|---|---|---|
| `b3` | 能效优先 | `service.optimize_dispatch`（B3+B4） | `b3-optimizer-v0.1` |
| `balanced` | 低负荷优先 | `UC.plan(UCConfig(balanced=True))` | `balanced-uc-v0.1` |
| `load_balanced` | 加载率均衡优先（机组间 D3） | `UC.plan(UCConfig(spread_balance=True, spread_tol=0.10))` | `load-balanced-lrb-v0.1` |

## 2. 输入
- `snapshot: StationSnapshot`（`as_of` + 逐机 `running/flow/discharge_pressure/igv/bov/quality_valid`）
- `forecast: ForecastResult`（`predicted_flow` + `upper_bound`）；可选 `forecast_seq=[需求×n]` 覆盖多步
- `health: list[HealthAssessment]`、`constraints: DispatchConstraints`（含 `reserve/demand_basis/rated/min`）
- `state: PolicyState`（最短开停锁）；`config_path`（默认 `configs/optimization.yaml`）；`timeout_ms`

## 3. 输出（JSON 可序列化 dict，节选）
`mode, status, is_fallback, execution_permission, as_of, required_flow_m3_min, target_combination,`
`device_actions[{device_id, action, target_flow_m3_min, load_ratio, running_after, estimated_power_kw}],`
`total_flow_m3_min, estimated_power_kw, estimated_energy_kwh, reserve_m3_min, transition_plan,`
`surge_status, surge_assessment, safety_blocked, reason_codes[], constraints[], latency_ms`。
`balanced`/`load_balanced` 额外含 `sequence`（多步组合）、`horizon_energy_kwh`。

## 4. 异常与安全回退
| 情形 | 行为 | status |
|---|---|---|
| `mode` 非法 | 抛 `ValueError` | — |
| 输入非法/模型缺失 | 探回 B1，`is_fallback=True` | `insufficient_data` |
| 预测缺失/过期 | 探回 B1 | `insufficient_data` |
| 容量/状态不可行 | 探回 B1 | `infeasible` |
| 求解超时（`timeout_ms`） | 探回 B1 | `timeout` |

统一：`execution_permission="simulation_candidate_only"`；回退候选在 `fallback_candidate`；**不发送任何 PLC 控制指令**。

## 5. 真实调用样例（摄自 `c_call_examples.json`，valid 段 2026-08-01 某时刻）
| mode | status | 组合 | 供气(m³/min) | 功率(kW) | 喘振 | latency(ms) |
|---|---|---|---|---|---|---|
| b3 | success | 4#+5# | 428.54 | 2339.62 | medium | 41.1 |
| balanced | success | 4#+5# | 428.54 | 2343.41 | medium | 404.1 |
| load_balanced | success | 4#+5# | 428.54 | 2441.70 | medium | 18.9 |

异常用例：`invalid_mode`→`ValueError`；`missing_forecast`→`insufficient_data`/`is_fallback=true`；`timeout_ms=-1`→`timeout`/`is_fallback=true`。

## 6. 依赖
`pandas/numpy/pyyaml/scikit-learn/lightgbm/joblib`；模型 `artifacts/models/power_model.json`；配置 `configs/{optimization,baseline,safety}.yaml`。

## 7. 未经 C 确认，不修改 C 协议
本包仅提供 B 侧可调用实现与样例；C 侧接入方式自定；未改动任何 C 代码/接口。
