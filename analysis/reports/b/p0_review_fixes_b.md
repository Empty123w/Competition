# B5 核心代码审查 P0 修正报告

- 负责人：成员 B ｜ 分支：`feat/b-optimization-aligned`（本地，**未推送**）
- 基准：`6e3b6e605cb5fdef44aad24bfba369cf0f4f5ab9`
- 范围：仅修复已确认的评测/约束语义问题；**未修改冻结 B1–B4**；未调参、未重选窗口、未 push/合并/PR
- 结果文件全量回归：**209 passed, 0 failed**

---

## 1. 已确认问题清单（位置 + 复现）

| 编号 | 问题 | 代码位置 | 复现 |
|---|---|---|---|
| P1-A | 单位混用：`p += surplus/K_VOL - leak*(p-P_ENV)` 未乘 dt，混合“每分钟物理量”与“每步经验差分” | `analysis/scripts/pressure_sim_dynamics.py:76` | 设 K_VOL 翻倍，单步 Δp 不按物理比例变化 |
| P1-B | 随机性不可复现：`seed=name.__hash__()%1000`，跨进程不稳；两策略噪声不同 | 同上 `:119` | 两次进程运行结果不同；两策略非同噪声 |
| P1-C | 反馈候选指标引用旧供气序列 | 同上 `:157` | `_metrics(pf, plans["multi_step"], demand)` |
| P1-D | 反馈“先整体仿真再改供气”，因果性不清 | 同上 `:146-152` | 需在线因果闭环 |
| P2-A | 事件匹配窗 `[s-120,e]` 与误报判定窗 `[s,e]` 不一致 → 事件前合法预警被判误报 | `surge_ew_validation_v2.py:124-144` | 构造事件前 10s 报警，被同时算“检出”和“误报” |
| P2-B | 起始次数漏算“第 0 秒已激活” | 同上 `:143`（`_alarm_onsets` 定义却未被使用） | alarm[0]=True 时未计 onset |
| P2-C | unknown 步被当作 TN/FN（`valid` 参数未使用） | 同上 `:120-127` | 传感缺失段 below=True/报警 False → 计为 FN |
| P3 | `plan()` 仅校验首步 min_run/stop 与过渡；未来步未独立复核 | `multistep_optimization.py`/`_v2` | 缺独立验证器 |
| P4-A | SEC 单位错：`power/flow` 是 **kW·min/m³**，却标为 kWh/m³（差 60 倍） | `load_ratio_definition.py:52` | 数值 5.1567 应为 0.0859 kWh/m³ |
| P4-B | 15/30/60 预测与 15min×4 步规划器的对齐未定义 | `forecast_adapter.py` | 缺 45min 点的无泄漏回退 |

## 2. 每项问题的最小复现测试

- P1：`tests/b5/test_pressure_sim.py`（6 项：dt 尺度、可复现噪声、CRN 同步、供气核算、缺供供气来源、闭环因果时序）。
- P2：`tests/b5/test_surge_ew_metrics.py`（5 项：事件前预警不计误报、首秒 onset、全程报警误报、unknown 屏蔽、时间级 FP/活跃时长）。
- P3：`tests/b5/test_constraint_verifier.py`（8 项：plan/plan_v2 通过独立验证、min-run/switch-budget/过渡缺口/容量违规被检出、不可行拒绝、首步一致）。
- P4：`tests/optimization/test_forecast_adapter.py`（+3：hold_previous 无未来泄漏、strict 抛错、30min 对齐）。

## 3. 修订后的独立脚本 / 最小修复

| 任务 | 交付 |
|---|---|
| 一 压力 | 新脚本 `analysis/scripts/pressure_sim_dynamics_v2.py`（约定：`p+=dt*((sup-dem)/K_VOL − LEAK*(p−P_ENV))+noise`；dt=15min 显式；CRN；固定种子 `SEED_BASE=12345`；反馈在线因果且指标用自身供气） |
| 二 EW | 新脚本 `analysis/scripts/surge_ew_validation_v3.py`（窗口统一、首秒 onset、unknown 屏蔽、分项误报） |
| 三 约束 | 新独立验证器 `analysis/scripts/constraint_verifier.py`（无共享枚举逻辑；返回带原因码的 violations） |
| 四 加载率/预测 | 最小修复 `load_ratio_definition.py`（SEC ×1/60，输出转 `_v2`）；`forecast_adapter.py` 新增 `demand_series_for_steps(missing_policy="hold_previous")` |

**原始结果与脚本保留不变**：`pressure_sim_dynamics.py/.json`、`surge_ew_validation_v2.py/.json`、`load_ratio_definition.py` 旧输出、`multistep_optimization*.py` 均未改动（仅脚本 P4 输出路径改到 `_v2`）。

## 4. 原结果与新结果逐项差异

### 4.1 压力（原 83.8% 保留）
| 项 | 原版 | v2 修订 | 归因 |
|---|---|---|---|
| B1 基准合格率 | 0.840 | **0.8396** | CRN + dt 显式（量级等价） |
| 优化合格率 | 0.838 | **0.8391** | 同上 |
| Δ(优化−B1) | −0.0021 | **−0.0005** | CRN 消除两策略噪声差 |
| 敏感性优化≥B1 比例 | 0.667 | 0.667 | 一致（仍不稳健） |
| 反馈候选合格率 | 0.812 | **0.9042** | 修正“引用旧供气” + 在线因果 |
| 反馈低压时长(min) | 3045→0 | **1170** | 因果闭环下并非全消除 |

### 4.2 EW 误报（原 25.1 次/h 保留）
| 项 | v2 | v3 修订 | 归因 |
|---|---|---|---|
| EW 事件级误报/h | **25.143** | **18.286** | 事件前合法预警不再计误报（窗口统一） |
| EW 时间级 FP 步 | — | 244 | 新增分项 |
| EW 报警活跃占比 | 0.361 | 0.361 | 一致 |
| B4 时间级召回 | 0.954 | **1.000** | unknown 步从混淆矩阵屏蔽 |
| B4 lead≥30s 覆盖 | 0.1667 | 0.1667 | 一致（valid） |
| B4 事件级误报 | — | **0** | 新增（起始均落在匹配窗） |

### 4.3 加载率 SEC 单位
| 设备 | 原值(误标 kWh/m³) | 修订(kWh/m³) | λ*/平台下界 |
|---|---|---|---|
| 4# | 5.1567 | **0.085945** | 1.0 / 0.8537（不变） |
| 5# | 4.9971 | **0.083285** | 1.0 / 0.9381（不变） |

- 超标比例不变（A: 51.0% / B: 47.1%）；仅单位与标签修正。

### 4.4 预测对齐
- `demand_series_for_steps(step=15,n=4)`：15/30/45/60 → 45min 缺时回退 **上一可用(30min)**，**不使用未来信息或 actual_flow**；`strict` 模式报错。

## 5. 全量回归测试结果
`tests/data tests/models tests/baseline tests/optimization tests/safety tests/b5` → **209 passed, 0 failed**（106.77s）。冻结 `src/air_station/{baseline,safety}`、`configs`、`tests/{baseline,safety}` 未改动。

## 6. 对 6% / 60% / 压力 / 预警指标的影响判断

| 指标 | 判断 | 依据 |
|---|---|---|
| 6% 能效 | **不受影响** | SEC 单位修正仅针对**加载率诊断**的比功率标签；B5 电量/供气 SEC 计算本就正确 |
| 60% 大马拉小车 | **不受影响** | 未改 DP 规划与逐机口径；验证器仅致**约束复核**更严，plan/plan_v2 序列**全部通过** |
| 压力合格率/波动 | **结论方向不变**（仍不稳健、优化不优于 B1） | v2 基准 Δ −0.0005、robust 0.667 |
| 喘振预警 | **结论不变**（B4 lead≥30 不足；EW 覆盖高但误报仍高） | v3：EW 18.3 次/h、lead≥30=1.0；B4 lead≥30=0.1667 |

- 均为**仿真/模型**层级，**非现场**；**未通过换场景或抬阈值制造改进**。

## 7. 尚不能修复的工程数据依赖

1. **独立耗气(demand)测点**与管网储气/容积、响应时延参数 → 压力反事实可信化。
2. **设定点阶跃受控试验**（秒-分钟级）→ 动态辨识。
3. **厂家喘振边界 / 真实喘振正例 / 秒级数据** → 预警真值与提前量的现场验证。
4. **A 历史预测**（开发 03-26~07-31、验证 08-01~08-20；15/30/60）→ 同口径连续对照（适配器已就绪）。
5. **CAL-02/03/04/05** 工程口径确认。

## 8. 复现

```bash
python3 analysis/scripts/pressure_sim_dynamics_v2.py
python3 analysis/scripts/surge_ew_validation_v3.py
python3 analysis/scripts/load_ratio_definition.py        # 输出 *_v2
python3 -m pytest tests/b5/test_pressure_sim.py tests/b5/test_surge_ew_metrics.py tests/b5/test_constraint_verifier.py tests/optimization/test_forecast_adapter.py -q
```
