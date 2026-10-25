# 阶段 C：API 接口说明

启动：`air-station-api`（默认 `http://0.0.0.0:8000`，根路径 `/` 返回能力清单，`/docs` 为 OpenAPI 文档）。

## 统一约定

- 成功响应含 `"status": "success"`；失败统一为：

```json
{
  "status": "error",
  "error": {
    "code": "E_DATA_MISSING",
    "message": "依赖的数据/配置缺失",
    "detail": {},
    "request_id": "...."
  }
}
```

- 错误码：`E_VALIDATION`(400/422) `E_DATA_MISSING`(404) `E_NOT_FOUND`(404)
  `E_METHOD`(405) `E_RUNTIME`(500) `E_INTERNAL`(500)。
- 每个响应返回 `X-Request-Id` 头，与 `error.request_id` 一致。
- 服务层异常（特征/模型/配置缺失、参数非法）在 API 边界统一转换，**不会伪装成 success**。

## 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 服务首页 / 能力清单 |
| GET | `/api/system/health` | 存活探针 |
| GET | `/api/system/info` | 服务信息与 MCP 工具列表 |
| GET | `/api/system/overview` | 运行总览（SCADA + 健康 + RUL + 喘振） |

## 模型服务

### POST `/api/forecast`

请求：`{"horizons": [15, 30, 60]}`（可省略，缺省 15/30/60）。

响应字段固定：`status, model_type, prediction_base_time, predictions[], elapsed_seconds`，
每项预测固定：`horizon_minutes, target_time, predicted_flow, lower_bound, upper_bound`。

### POST `/api/health`

响应字段固定：`status, validation_mode, evaluation_time, devices[]`，设备固定 9 字段。

### POST `/api/rul`（GET `/api/health/rul`）

设备剩余寿命估计（模拟）。响应字段固定：`status, validation_mode, evaluation_time,
devices[]`，`validation_mode=simulation`，每设备固定 5 字段：`device_id,
estimated_rul_hours, lower_bound_hours, upper_bound_hours, method`。

### POST `/api/surge`

调用 B 的 `assess_surge_risk`（代理评分/统计边界，非真实喘振线）。
响应为 B 的 `SurgeAssessment`（`status, boundary_version, boundary_source, validation_mode,
devices[], safety_blocked, counts, reason_codes, capability`）另加 `station_risk_level` 与 `c_meta`。

### POST `/api/dispatch`

请求：

```json
{
  "mode": "b3",
  "demand_flow_m3_min": 480.0,
  "horizon_minutes": 15,
  "timeout_ms": null,
  "persist": true
}
```

- `mode ∈ {b3 能效优先, balanced 低负荷优先, load_balanced 加载率均衡, joint_nompc 多目标(B 最终候选)}`。
- `demand_flow_m3_min` 省略时自动调用 A 的预测接口取下界/上界。
- `persist=true` 时方案落库并返回 `plan_id`。

响应为 B 的 `plan_dispatch` 结果（`status, is_fallback, execution_permission, as_of,
required_flow_m3_min, target_combination, device_actions[], total_flow_m3_min,
estimated_power_kw, surge_status, safety_blocked, reason_codes[], latency_ms` 等），
另加 C 侧 `c_meta`（`candidate_usable / needs_human_review / execution_allowed(=false)` /`elapsed_seconds`）。
B 输出恒为 `execution_permission="simulation_candidate_only"`，不含任何真实执行许可。

| GET | `/api/dispatch/plans` | 历史调度方案列表 |

## 控制 / SCADA / EAM

### POST `/api/control/command`

```json
{
  "action": "plc_command",
  "device_id": "4#",
  "action_type": "set_load",
  "target_flow_m3_min": 240.0,
  "persist": true
}
```

`action` 取值：`scada_query` | `scada_history` | `plc_command` | `execute_plan` |
`eam_maintenance` | `work_order` | `command_status`。

- `plc_command` 返回 `accepted, command_id, command_status, feedback, elapsed_ms, sla_ok`。
- 被联锁拒绝时 `accepted=false` 且带 `rejected_reason`。
- `execute_plan` 传入 `plan`（来自 `/api/dispatch`）即可整份方案闭环执行。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/control/commands/{id}` | 指令执行状态 |
| GET | `/api/scada/realtime` | 站级实时快照（模拟） |
| GET | `/api/scada/history?device_id=4#&minutes=60` | 单机历史序列 |
| GET | `/api/eam/maintenance?device_id=4#` | 维保计划（模拟 EAM） |
| POST | `/api/workorders` | 生成工单 `{device_id, reason?, priority?}` |
| GET | `/api/workorders` | 工单列表 |

## Agent

### POST `/api/agent/chat`

```json
{"message": "3号机组振动偏高，请优化调度", "execute": false, "session_id": null, "persist": true}
```

响应：`status, session_id, intent, intent_confidence, engine, answer, tools_used[],
explain_chain[], follow_up[], plan, tool_results, elapsed_seconds, context`。

- `intent ∈ {forecast_query, health_query, surge_query, dispatch_optimize,
  control_execute, workorder, status_overview, unknown}`。
- `execute=true` 时 `control_execute` 会真正向模拟 PLC 下发指令。
- 传入 `session_id` 可进行多轮对话（携带上下文）。

| GET | `/api/agent/sessions` | 会话列表 |
| GET | `/api/agent/sessions/{id}` | 会话详情 |

## 指标

| GET | `/api/metrics/comparison` | 最新调度节能对比 + 预测/健康指标 |
