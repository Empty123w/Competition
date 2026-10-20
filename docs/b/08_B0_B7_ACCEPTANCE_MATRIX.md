# B0–B7 最终任务验收矩阵

依据：《B成员人工基线调度优化与喘振预警详细任务书》v1.1（`docs/B成员人工基线调度优化与喘振预警详细任务书.md`）。
状态：**已完成 / 部分完成 / 未完成 / 需外部联调**。B 侧完成 ≠ C/D 联调完成。

| 阶段 | 原始任务 | 实际实现 | 文件路径 | 测试证据 | 状态 | 尚需联调 | 责任人 |
|---|---|---|---|---|---|---|---|
| **B0** | 数据、接口、实验设置 | 数据体检、假设登记、时间切分、A6 接入验收 | `analysis/reports/b/{data_fit_report.md,assumptions_register.md}`,`optimization/forecast_adapter.py` | `tests/optimization/test_forecast_adapter.py`,`test_a6_integration.py` | 已完成 | A 缺字段（独立用气/储气）待确认 | B |
| **B1** | 传统人工基线 | 规则调度 + 回退 | `baseline/rules.py`,`configs/baseline.yaml` | `tests/baseline/*` | 已完成 | — | B |
| **B2** | 机组性能与管网模型 | 功率模型(MAE 选优)+压力近似 | `optimization/{performance,pressure}.py`,`artifacts/models/power_model.json` | `tests/optimization/test_performance.py`,`test_pressure.py` | 部分完成 | 压力反事实不足（缺数据） | B |
| **B3** | 多机优化调度 | 组合枚举+负荷分配+回退 | `optimization/{optimizer,service,constraints}.py`、`unit_commitment.py` | `tests/optimization/test_optimizer.py`,`test_service.py`,`test_unit_commitment.py` | 已完成 | — | B |
| **B4** | 喘振与安全约束 | 代理边界+分级+阻断+过渡 | `safety/{boundaries,surge}.py`,`configs/safety.yaml` | `tests/safety/test_surge.py`,`test_boundaries.py` | 部分完成 | 无厂家真实喘振线/正例 | B |
| **B5** | 同工况回测与 KPI | dev/valid/test+全历史统一汇总 | `analysis/scripts/{multikpi_harness,b5_unified_summary,b5_load_ratio_all}.py` | `tests/b5/*` | 已完成 | 官方评分窗口/公式待确认 | B |
| **B6** | 封装、测试与 C 集成 | `b_api`+`plan_dispatch`+B01–B20 用例+样例 | `optimization/{b_api,dispatch_api}.py`,`tests/optimization/test_b01_b20_acceptance.py`,`analysis/reports/b/{integration_B_to_C.md,C_INTERFACE.md}` | B01–B20 20 passed；三模式 6 passed | **B 侧已完成** | **C 侧 MCP/PLC/端到端未完成** | B+C |
| **B7** | 两典型场景与 D 材料 | 场景脚本+数据+负例+图表数据 | `analysis/scripts/b7_scenarios.py`,`analysis/reports/b/b7/*`,`docs/b/06_B_TO_D_HANDOFF.md` | 脚本可独立重放（见下） | **B 侧已完成** | D 前端联调、C 解释链 | B+D |

## B01–B20 测试用例对照（`tests/optimization/test_b01_b20_acceptance.py`，20 passed）
| 用例 | 期望 | 实测断言 | 状态 |
|---|---|---|---|
| B01 正常/中等需求 | 基线与优化均有完整方案 | `test_B01_normal_full_plan` | 通过 |
| B02 需求超双机能力 | `INFEASIBLE_CAPACITY`，无动作 | `test_B02_over_capacity_infeasible` | 通过 |
| B03 需求下降但备用不允许停机 | 保留必要机组 | `test_B03_reserve_prevents_shutdown` | 通过 |
| B04 单机健康高风险/限载 | 尊重限载 | `test_B04_health_derate_respected` | 通过 |
| B05 关停过渡中断供气 | 拒危险步骤 | `test_B05_transition_infeasibility` | 通过 |
| B06 最短运行未到请求停机 | 阻断 | `test_B06_min_run_lock` | 通过 |
| B07 加载率超冻结上限 | 约束失败 | **无冻结上限（target pending）** | 部分（待 CAL-04） |
| B08 压力预计超带 | 阻断/不确定性 | `test_B08_pressure_counterfactual_flagged` | 通过 |
| B09 高喘振代理风险 | `safety_blocked=true` | `test_B09_high_surge_blocked` | 通过 |
| B10 缺边界/关键值 | `unknown`，不默认 low | `test_B10_unknown_not_low` | 通过 |
| B11 预测过期 | `STALE_FORECAST` | `test_B11_stale_forecast` | 通过 |
| B12 零流量/无效质量 | 无 NaN/∞ | `test_B12_no_nan_zero_flow` | 通过 |
| B13 两机同型异性 | 按设备模型选 | `test_B13_device_specific_selection` | 通过 |
| B14 历史超越铭牌 | 报告超额 | `test_B14_observed_over_nameplate_recorded` | 通过 |
| B15 超时/模型缺失 | 明确状态+回退复检 | `test_B15_timeout_or_model_missing` | 通过 |
| B16 同输入重复 | 稳定 | `test_B16_repeatable` | 通过 |
| B17 不公平比较 | 拒绝 | `test_B17_unfair_comparison_guard_exists` | 通过 |
| B18 真实回放 3# | 不存在 | `test_B18_device_3_absent` | 通过 |
| B19 30s 提前预警 | 仅仿真证据 | `test_B19_no_real_lead_claim` | 通过 |
| B20 C 调两函数 | JSON 契约一致 | `test_B20_two_functions_json_contract` | 通过 |

## 关键结论
- **B0–B5**：B 侧完成（B2/B4 标注为"工程近似/代理，存在数据限制"）。
- **B6/B7**：**B 侧封装/测试/场景数据完成**；**C 的 MCP/PLC/端到端** 与 **D 的前端重放** 属**未完成的外部联调**。
- **未完成但明确标注**：官方能效公式/人工基线、加载率定义、压力合格带与真实数据、真实喘振标签、C 端到端时延。

## B7 场景独立重放证据
```bash
python3 analysis/scripts/b7_scenarios.py
# 场景一: analysis/reports/b/b7/scenario1_{load_drop_ledger.csv,forecast.csv,summary.json}
# 场景二: analysis/reports/b/b7/scenario2_events.json (unit5_derate=success; neg_capacity_insufficient=INFEASIBLE; neg_high_surge=safety_blocked)
```
三机"3号机"场景：优化器真实设备仅为 {4#,5#}（`C.DEVICE_IDS`），**未实现三机**；如演示 3# 需独立仿真扩展，当前**未完成**（不混入真实统计）。
