# C/D 联合演示交付（B 侧可调用内容）

本页面向 C/D：给出可复现的场景输入、运行脚本、调度接口、输出样例与命令。**仅 B 侧，未改 C/D 代码**；未声称完成任何未经测试的 C 端到端功能。

## 1. 演示场景（5 个，固定种子）
| # | 场景 | 目的 | 输入 | 运行 |
|---|---|---|---|---|
| 1 | 正常负荷 | 自动选择机组组合 | `SCENARIOS["normal"]`（base 430±40） | `simulate(mode, SCENARIOS["normal"])` |
| 2 | 低负荷 | 减少大马拉小车 | `SCENARIOS["low"]`（base 250±25） | `simulate("balanced"/"load_balanced", …)` |
| 3 | 负荷突增/高峰 | 维持管网压力 | `SCENARIOS["step"]`（300→480）/`["peak"]`（500） | `simulate(mode, …)` |
| 4 | 设备异常 | B4 安全回退 | `SCENARIOS["fault"]`（5# 失效） | `simulate(mode, …)` |
| 5 | 自然语言选目标 | 能效/负荷/压力/安全权衡 | 目标词 → mode 映射 | 见 §3 |

统一定义在 `src/air_station/simulation/closed_loop.py`；批量结果 `analysis/reports/b/simulation/sim_closed_loop.json`；逐点序列 `analysis/reports/b/simulation/sim_ledger.csv`（可直接用于画图）。

## 2. 调度接口（3 模式）
```python
from air_station.optimization import dispatch_api as API
out = API.plan_dispatch(mode, snapshot, forecast, health, constraints, state=state)
# mode ∈ {"b3","balanced","load_balanced"}; 全部 execution_permission="simulation_candidate_only"
```
- 输入：`StationSnapshot`（as_of/母管压力/逐机 running/flow/压力/IGV/BOV/quality）、`ForecastResult`（predicted/upper）、`health`、`DispatchConstraints`、`PolicyState`。
- 输出：`status/target_combination/device_actions/total_flow_m3_min/estimated_power_kw/surge_status/safety_blocked/latency_ms`（详见 `ALGORITHMS_AND_API.md`）。
- 安全回退：任何失败→`is_fallback=true`、返回 B1 候选；**不发送 PLC 控制指令**。

## 3. 自然语言目标 → 模式映射（供 C/D 演示）
| 用户意图（示例） | 推荐 mode | 期望权衡 |
|---|---|---|
| "优先省电/提高综合能效" | `b3` | SEC 最优，低负荷改善较小 |
| "减少大马拉小车/低负荷优化" | `balanced` | 低负荷大幅下降（~76%），SEC 略降 |
| "机组负荷均衡/加载率达标" | `load_balanced` | 机组间 D3 ≈100%，SEC 居中 |
| "设备异常/安全优先" | 任意 mode（触发 `is_fallback`） | 回退 B1，供气不足如实报告 |

## 4. 输出样例
- 三模式真实调用（valid 段某时刻）：见 `c_call_examples.json`
  （b3 功率 2339.6kW/供气 428.54/喘振 medium/41ms；balanced 2343.4kW/404ms；load_balanced 2441.7kW/19ms）。
- 异常/回退：`invalid_mode→ValueError`；`missing_forecast→insufficient_data,is_fallback`；`timeout_ms=-1→timeout,is_fallback`。
- 闭环场景指标：见 `../SIM_CLOSED_LOOP_STUDY.md`（normal/low 压力合格率 100%）。

## 5. 复现命令
```bash
# 单元/集成测试
python3 -m pytest tests/optimization tests/simulation -q
# 三模式调用样例与异常用例
python3 analysis/scripts/c_call_examples.py         # -> analysis/reports/b/delivery/c_call_examples.json
# 闭环场景批量对比 (5 场景 × 4 策略 + 敏感性)
python3 -u analysis/scripts/sim_closed_loop_study.py
# 单场景交互
python3 -c "from air_station.simulation.closed_loop import simulate,SCENARIOS; \
print(simulate('load_balanced',SCENARIOS['low'])['metrics'])"
```

## 6. 限制
- 仿真为**工程假设**（K_VOL/泄漏/合格带/独立需求均假设）；非现场达标。
- 未完成 C 端到端集成；端到端时延与 PLC 链路需 C 实测后确认。
