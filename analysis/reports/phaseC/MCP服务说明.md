# 阶段 C：MCP 服务说明

MCP Server 暴露 5 个必备服务（赛题要求）与编排工具，可被 COSMO Code / 天智平台
MCP 框架接入。所有工具仅调用模拟接口，不连接真实设备。

## 启动

```bash
# stdio（默认，供 MCP 客户端以子进程方式拉起）
air-station-mcp

# streamable-http（供容器/远程接入，默认端口 8765）
python -m air_station.mcp_servers.server --http
```

stdio 客户端配置示例（等价 JSON）：

```json
{
  "mcpServers": {
    "air-station-agent": {
      "command": "python",
      "args": ["-m", "air_station.mcp_servers.server"]
    }
  }
}
```

## 工具清单

| 工具 | 对应赛题 MCP 服务 | 说明 |
|---|---|---|
| `forecast_load` | 负荷预测 | 未来 15/30/60 分钟用气负荷（点预测 + 区间） |
| `dispatch_optimize` | 调度优化 | 开机组合 + 加载率分配 + 节能对比（可解释，含 RUL 软约束） |
| `health_diagnose` | 健康诊断 | 4#/5# 健康评分、风险等级、检修建议 |
| `rul_estimation` | 剩余寿命 | 模拟退化 RUL（`validation_mode=simulation`） |
| `surge_warning` | 喘振预警 | 低/中/高 风险分级与预警建议（机理+模拟） |
| `control_command` | 控制指令 | SCADA 查询 / PLC 下发 / 方案执行 / EAM / 工单 |
| `create_work_order` | 辅助 | 生成设备维保工单 |
| `orchestrate` | 辅助 | 自然语言编排（Agent 工作流） |

## 工具入参

### forecast_load

```json
{"horizons": [15, 30, 60]}
```

### dispatch_optimize

```json
{"mode": "b3", "demand_flow_m3_min": 480.0, "horizon_minutes": 15, "timeout_ms": null}
```

- `mode ∈ {b3 能效优先, balanced 低负荷优先, load_balanced 加载率均衡}`；
- `demand_flow_m3_min` 省略时自动调用预测服务；
- 输出即 B 的 `plan_dispatch` 结果（`device_actions/target_combination/estimated_power_kw/
  surge_status/safety_blocked/reason_codes/execution_permission` 等），另加 `c_meta`。

### health_diagnose / rul_estimation / surge_warning

无必填参数（读取 `data/processed` 与 `data/simulated` 最新状态）；
`surge_warning` 输出 B 的代理喘振评估。

### control_command

```json
{"action": "plc_command", "device_id": "4#", "action_type": "set_load",
 "target_flow_m3_min": 240.0, "persist": true}
```

`action`：`scada_query` | `scada_history` | `plc_command` | `execute_plan` |
`eam_maintenance` | `work_order` | `command_status`。
`execute_plan` 传入 B 的调度输出，经准入闸门后在模拟 PLC 执行。

### orchestrate

```json
{"user_input": "3号机组振动偏高，请优化调度", "execute": false, "persist": true}
```

## 调用示例（Python，进程内）

```python
from air_station.mcp_servers import tools

print(tools.dispatch_optimize(mode="b3", demand_flow_m3_min=480.0)["estimated_power_kw"])
print(tools.orchestrate("预测未来15分钟负荷", persist=False)["answer"])
```

## 字段口径

- 预测、健康、RUL 成功响应的字段名与层级与 A 的冻结接口完全一致（见
  `analysis/reports/A_to_C_服务接口.md`）。
- 调度为 B 的 `simulation_candidate_only` 输出；喘振为 B 的代理评估（`validation_mode=simulation`）。
