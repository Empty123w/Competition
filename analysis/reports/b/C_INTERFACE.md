# B 模块 → C 模块 调度接口交付（不修改 C 协议）

代码：`src/air_station/optimization/dispatch_api.py`
入口：`plan_dispatch(mode, snapshot, forecast, health, constraints, *, state=None, forecast_seq=None, config_path=..., timeout_ms=None) -> dict`

## 1. 三种可选模式
| mode | 语义 | 底层实现 | policy_version |
|---|---|---|---|
| `"b3"` | 能效优先 | `service.optimize_dispatch`（B3+B4） | `b3-optimizer-v0.1` |
| `"balanced"` | 低负荷优先 | `unit_commitment.plan(cfg=UCConfig(balanced=True))` | `balanced-uc-v0.1` |
| `"load_balanced"` | 加载率均衡优先（D3） | `unit_commitment.plan(cfg=UCConfig(spread_balance=True))` | `load-balanced-lrb-v0.1` |

三者共用同一：输入快照/预测/健康/约束、B4 安全边界、extra 5% 备用、爬坡、最短开停机、过渡供气连续性、回退口径。

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

## 4b. 状态判定规则（统一，推荐 C 直接使用）
**不要按 `status` 字符串大小写自行匹配**；用 `b_api` 提供的判定函数（已导出，不改返回字段/取值）：

| 判定 | 函数 | 语义 |
|---|---|---|
| 求解成功且非回退 | `b_api.is_success(out)` | `status.lower()=="success"` 且 `is_fallback==false`（大小写不敏感） |
| 是否回退 | `b_api.is_fallback_result(out)` | `is_fallback==true`（失败后回退 B1，**不得视为优化成功**） |
| 安全阻断 | `b_api.is_blocked(out)` | `safety_blocked==true`（喘振 high/unknown，**禁止执行**） |
| 容量/状态不可行 | `b_api.is_infeasible(out)` | `status.lower()=="infeasible"` |
| 可展示的仿真候选 | `b_api.candidate_usable(out)` | `is_success && !is_blocked`（仍**非执行许可**） |
| 需人工复核 | `b_api.needs_human_review(out)` | 失败/回退/阻断任一为真 |
| 执行许可 | `b_api.execution_allowed(out)` | **恒为 False**（B 不提供真实设备执行许可） |

**状态取值现状（保留，不重命名）**：`"success"`；失败态为 `"INFEASIBLE"`/`"INSUFFICIENT_DATA"`/`"INVALID_INPUT"`（大写）与回退路径的 `"timeout"/"fallback"/"infeasible"`（小写）。原因码（`reason_codes`）为大写常量（如 `TIMEOUT`/`INFEASIBLE_CAPACITY`/`FORECAST_MISSING_HOLD`）。**`status` 与 `reason_codes` 是两个不同字段，勿混用。**

**四类结果**：正常调度(`is_success && !is_blocked`) / 容量不可行(`is_infeasible`) / 安全回退(`is_fallback_result`) / 安全阻断(`is_blocked`)；**后三类均不得解释为可正常执行**。
- `timeout_ms` 现对**三模式一致生效**（b3 亦遵守）。

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
