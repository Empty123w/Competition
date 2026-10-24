---
knowledge_id: KB-IFACE-001
title: 系统接口、MCP 工具与 Agent 路由
category: 系统接口
keywords:
  - 接口
  - MCP
  - API
  - 调用
  - 返回字段
  - forecast_json
  - health_json
  - rul_json
  - knowledge_search
  - Agent
  - 意图
source_paths:
  - src/air_station/mcp_servers/tools.py
  - src/air_station/mcp_servers/server.py
  - src/air_station/api/app.py
  - src/air_station/agent/intent.py
  - analysis/reports/A_to_C_服务接口.md
origin: project_repository
validation_mode: real_data
last_verified: 2026-10-24
---

## 结论

系统对外提供运行时函数、MCP 工具与 REST API。运行时接口包括 `forecast_json()`、`health_json()`、`rul_json()`、`maintenance_json()`。

## 输出

- 关键 MCP 工具：`forecast_load`、`health_diagnose`、`rul_estimation`、`dispatch_optimize`、`surge_warning`、`control_command`、`create_work_order`、`maintenance_warning`、`knowledge_search`、`orchestrate`。
- API：`POST /api/forecast`、`POST /api/health`、`POST /api/rul`、`POST /api/dispatch`、`POST /api/control/command`、`GET /api/system/overview`、`GET /api/maintenance/status`、`POST /api/knowledge/search`、`POST /api/knowledge/ask`、`GET /api/knowledge/status`。

## 方法或规则

- 成功响应字段固定，封装方不得改名；失败由服务层错误处理，不伪造 `status=success`。
- Agent 路由优先级：实时预测/健康/RUL/调度/喘振/控制 → 动态工具；方法/含义/规则/依据/输入输出 → 知识库；组合请求 → 先动态再解释；未知 → 知识库；知识库无依据 → 拒答。

## 使用方式

```python
from air_station.models.forecasting.service import forecast_json
from air_station.knowledge.service import knowledge_json
```

## 证据与来源

- `src/air_station/mcp_servers/tools.py`、`src/air_station/api/app.py`、`src/air_station/agent/intent.py`、`analysis/reports/A_to_C_服务接口.md`

## 适用范围和限制

控制类能力仅作用于**模拟** PLC/SCADA/EAM；真实设备接口未接入。
