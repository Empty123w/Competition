# B 模块 → C 模块 调度接口交付（不修改 C 协议）

代码：`src/air_station/optimization/dispatch_api.py`
入口：`plan_dispatch(mode, snapshot, forecast, health, constraints, *, state=None, forecast_seq=None, config_path=..., timeout_ms=None) -> dict`

## 1. 两种可选模式
| mode | 语义 | 底层实现 | policy_version |
|---|---|---|---|
| `"b3"` | 能效优先 | `service.optimize_dispatch`（B3+B4） | `b3-optimizer-v0.1` |
| `"balanced"` | 低负荷优先 | `unit_commitment.plan(cfg=UCConfig(balanced=True))` | `balanced-uc-v0.1` |

两者共用同一：输入快照/预测/健康/约束、B4 安全边界、extra 5% 备用、爬坡、最短开停机、过渡供气连续性、回退口径。

## 2. 统一输入
- `snapshot: StationSnapshot`（`as_of` + 逐设备 `running/flow/pressure/igv/quality_valid`）
- `forecast: ForecastResult`（`predicted_flow` + `upper_bound`）；`balanced` 可选 `forecast_seq=[4 个需求值]` 覆盖多步需求
- `health: list[HealthAssessment]`、`constraints: DispatchConstraints`（含 `reserve`/`demand_basis`/`rated`/`min`）
- `state: PolicyState`（最短开停锁跟踪）；`config_path`（默认 `configs/optimization.yaml`）
- `timeout_ms`（默认取 `optimization.yaml: dispatch.timeout_seconds`）

## 3. 统一输出（JSON 可序列化 dict）
关键字段：`mode, status, is_fallback, execution_permission, as_of, required_flow_m3_min,`
`target_combination, device_actions[{device_id, action, target_flow_m3_min, load_ratio, running_after, estimated_power_kw}],`
`total_flow_m3_min, estimated_power_kw, estimated_energy_kwh, reserve_m3_min, transition_plan,`
`surge_status, surge_assessment, safety_blocked, reason_codes, constraints[], latency_ms`。
`balanced` 额外含 `balanced=true`、`sequence`（4 步组合）、`horizon_energy_kwh`。

## 4. 安全回退
任何失败（输入非法/模型缺失/预测缺失或过期/容量或状态不可行/超时）→ 统一走 `_fallback`：
- `is_fallback=true`，`execution_permission="simulation_candidate_only"`，`status ∈ {timeout, insufficient_data, infeasible, fallback}`（**非 success**）；
- 回退候选为 **B1 规则 + 复检**，放入 `fallback_candidate`，同样附 B4 喘振评估；
- 超时由 `timeout_ms` 控制（`timeout_ms<=0` 视为必触发），保证 ≤30s 目标。

## 5. 运行命令
```bash
# 单元/集成测试
python3 -m pytest tests/optimization/test_dispatch_api.py -q

# 交互冒烟
python3 -c "from air_station.optimization import dispatch_api as A; print(A.MODES)"
```

## 6. 性能（`kpi_latency.json`）
| 模式 | p50 | p95 | max |
|---|---|---|---|
| B3 | 37.1 ms | 48.9 ms | 97.4 ms |
| balanced | 18.0 ms | 100.8 ms | 417.9 ms |
超时回退验证：`timeout_fallback_ok=true`。均远低于 30s 目标。
> 注：`balanced` 依赖进程级功率缓存 `_POWER_CACHE`（按模型路径）；C 侧可复用同一进程以受益。

## 7. 边界（诚实声明）
- 仅提供**仿真候选**（`execution_permission=simulation_candidate_only`），**不自动现场执行**；
- 压力反事实不支持（`RC_PRESSURE_COUNTERFACTUAL`）；喘振为代理评估；
- 未改 C 代码/协议；C 侧接入方式自定。
