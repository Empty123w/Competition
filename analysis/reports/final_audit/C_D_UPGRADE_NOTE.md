# C/D 升级说明 (B 组封版)

## 一、B6 新增调度模式 (向后兼容)
- `plan_dispatch(mode, ...)` 现支持 `mode ∈ {b3, balanced, load_balanced, joint_nompc}`; **默认仍为 b3**。
- 旧三模式行为与返回字段不变; 新字段: `mode="joint_nompc"`, `degraded`(bool, 需求上界超容量时满容量降级)。
- 始终 `execution_permission="simulation_candidate_only"`, `execution_allowed=false`。
- 失败语义不变: 不可行 -> `status="INFEASIBLE"` + `is_fallback=true`; 超时 -> `status="timeout"`; 缺预测 -> `status="INSUFFICIENT_DATA"`; 安全阻断 -> `safety_blocked=true`(不执行)。

### C 调用示例
```python
from air_station.integration.dispatch_bridge import dispatch_plan
out = dispatch_plan(mode="joint_nompc", demand_flow_m3_min=None, horizon_minutes=15)
out["c_meta"]["candidate_usable"]   # 可下发(模拟)前提
out["execution_allowed"]            # 恒 False
```
- C 的 `integration/dispatch_bridge.py` **无需改代码**即可传 `mode="joint_nompc"` (已 dry-run 验证)。
- 建议(可选): 更新 `api/schemas.py`、`mcp_servers/tools.py` 中 mode 描述, 加入 `joint_nompc`。

## 二、D 展示建议 (当前无前端)
- 能效: 用 **pooled service_equiv SEC 降 7.84%**(并标注 dev 10.49/valid 3.84/test 2.98, 主口径 10793 步)。
- 加载率: 展示 **偏离度 平均 8.10pp / P95 9.86pp / 最大 9.999pp** 与"双机达标率 100%"; **不要用"D3=100%"代替"偏离度"**。
- 压力: valid 0.9998 / test 0.9957 / dev 0.8592 (注明 dev 容量受限)。
- 喘振: 标注"1Hz 人工合成仿真 + 代理边界"; Acc 0.900, 提前≥30s 覆盖 0.914, 误报 7.2/h。
- 安全: 高危误操作 0; 展示 `safety_blocked` / `is_fallback` / `execution_permission`。
- 所有数值标注数据来源与证据等级(②真实驱动回测 / ③真实驱动闭环 / ④人工合成仿真 / ⑥假设估算)。
