# B → D 正式交接（给 D 组前端与比赛材料负责人）

> 所有图表数据标明来源：`real_history` / `model_estimate` / `simulation` / `proxy` / `weak_event`。禁止把代理边界画成厂家真实喘振曲线。

## 1. 可展示的算法功能
- 三种调度模式切换：`b3` 能效优先 / `balanced` 低负荷优先 / `load_balanced` 加载率均衡（主方案 LRB-t10）。
- 展示"能效 ↔ 低负荷 ↔ 加载率 ↔ 安全"的权衡与解释文本。

## 2. 两个规定场景（可独立重放）
- **场景一 负荷下降节能调度**：`analysis/scripts/b7_scenarios.py` → `analysis/reports/b/b7/scenario1_*.{csv,json}`。
  窗口 2026-07-02 10:45~07-03 01:30；需求 223.8→119.2 m³/min。
- **场景二 设备异常安全重调度**：`analysis/reports/b/b7/scenario2_events.json`（含负例）。

## 3. 数据文件与字段
| 文件 | 内容 | 关键字段 | 单位 | 来源 |
|---|---|---|---|---|
| `b7/scenario1_load_drop_ledger.csv` | 逐点对比 | t, demand_m3min, `{s}_{f4,f5,flow,power,lr4,lr5,low4,low5,on4,on5}` | m³/min, kW, - | real_history + model_estimate |
| `b7/scenario1_forecast.csv` | A 预测 | as_of,target_time,horizon_minutes,predicted_flow,lower_bound,upper_bound | m³/min | A 预测 |
| `b7/scenario1_summary.json` | 同工况节能 | energy/supply/sec/low + 降幅 | — | real_history |
| `b7/scenario2_events.json` | 异常重分配 + 负例 | status,target_combination,device_actions,surge{...},reason_codes | — | simulation_fixture + proxy |
| `simulation/sim_ledger.csv` | 闭环 5 场景逐点 | scenario,strategy,k,demand,supply,blow,pressure,power_kw,f4,f5,fallback,vent_m3,unmet_m3,clamp | m³/min,MPa,kW | simulation |
| `delivery/c_call_examples.json` | 三模式调用样例 | modes/error_cases | — | real fixture |
| `delivery/load_ratio_all.json` | 加载率 D1/D2/D3 | 各段×策略 | 比例 | model_estimate |

## 4. 能效与加载率展示
- 能效：SEC 降幅（全历史 服务等价：LRB 6.39%、bal 7.50%、B3 7.71%）；
- 加载率：三口径并列（D1 额定/D2 效率平台/D3 机组间），**标注官方未定义**；LRB D3≈100%。

## 5. 压力 / 喘振 / 安全展示
- 压力：闭环仿真的压力曲线与合格率（normal/low=100%，peak/step/fault 未达，**标注容量受限**）；历史 SCADA 分布（需标 `real_history`）。
- 喘振：风险等级与裕度（`proxy`，非厂家线）。
- 安全回退：`is_fallback`/`safety_blocked` 事件（`scenario2_events.json`）。

## 6. 前端切换三模式
调用 `plan_dispatch(mode, ...)`；界面按钮 → mode 切换；返回 JSON 直接渲染（动作/流量/功率/喘振/原因）。

## 7. 推荐可视化图表
算法决策流程图；人工 vs 优化对比表；逐机流量/功率曲线（`scenario1_*`）；加载率分布；母管压力曲线（`sim_ledger.csv`）；喘振风险示意图（裕度）；安全阻断事件表。

## 8. 两场景演示流程
1. 场景一：展示需求下降 → B1 保持双机低载（低负荷机分钟 1800）→ LRB 均衡/优化（15）→ 节能柱状 + SEC；
2. 场景二：5# 不可用 → 单机 4# 重分配（成功）；切到容量不足负例（520）→ 显示不可行；切到高喘振负例 → `safety_blocked=true`。

## 9. PPT/视频可引用的数字（内部口径）
- 综合能效：LRB 全历史服务等价 **6.39%**、bal 7.50%、B3 7.71%；
- 大马拉小车：bal 77.2%、LRB 61.8%；
- 加载率 D3：LRB ≈100%；
- 单次求解：毫秒级（p50 ≤41ms）；
- 压力仿真：normal/low 合格率 100%、波动降 ~89%（low）。
> 必须注明：**内部口径、仿真或历史回测**，非官方得分。

## 10. 禁止混淆
- 不把仿真压力合格率写成"现场 99.5% 达标"；
- 不把代理喘振边界写成厂家真实曲线；
- 不把 LRB 数字写成 B3 数字；
- 不把少供气写成节能。

## 11. D 必须完成的前端联调验收
接入 B 输出 JSON 字段；两场景可完整重放；正确展示"无解/高危阻断"；三成员使用**同一指标文件**。
