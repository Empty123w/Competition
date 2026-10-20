# B6 集成说明（integration_B_to_C）

- 版本：`b-api-v1.0`；策略版本 `b3-optimizer-v0.1 / balanced-uc-v0.1 / load-balanced-lrb-v0.1`。
- 入口：`air_station.optimization.b_api`（稳定导出）与 `dispatch_api.plan_dispatch`（统一三模式）。
- 契约对象：`contracts.{StationSnapshot, ForecastResult, HealthAssessment, DispatchConstraints, PolicyState, DispatchResult, SurgeAssessment}`。
- 配置由参数传入（`config_path` 默认相对项目根），**不猜测当前工况**。
- 权限：所有输出 `execution_permission="simulation_candidate_only"`，**无 PLC 写路径**。

## 六类样例（固定 fixture）
`analysis/scripts/c_call_examples.py` 生成 `analysis/reports/b/delivery/c_call_examples.json`：
1. 正常（三模式 success）；
2. 输入非法（mode）→ `ValueError`；
3. 预测缺失 → `INSUFFICIENT_DATA` + `is_fallback`；
4. 超时 → `TIMEOUT` + `is_fallback`；
5. 容量不可行（见 `b7/scenario2_events.json: neg_capacity_insufficient`）；
6. 高喘振 → `safety_blocked`（见 `neg_high_surge`）。

## 测试
`tests/optimization/test_b01_b20_acceptance.py`（B01–B20）、`tests/optimization/test_dispatch_api.py`（三模式+异常）、`tests/safety/test_surge.py`。

## 依赖
`pandas numpy pyyaml scikit-learn lightgbm joblib`；模型 `artifacts/models/{power_model,pressure_model,surge_boundary}.json`。

## C 侧待做（未完成）
MCP 包装、"预测→健康→调度→喘振→安全"协议级联调、端到端时延实测、PLC 放行策略。**B 侧不得声称已完成。**
