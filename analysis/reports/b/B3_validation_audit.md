# B3 能耗量纲与优化结果审计报告

- 阶段：B3 验收补充审计
- 负责人：成员 B
- 分支：`feat/b-optimization-aligned`
- 日期：2026-10-18
- 范围：能耗计算、B1/B3 公平性、优化器正确性；不改动 A，不重新开发优化器

---

## 1. 电量计算审计

| 检查项 | 结论 |
|---|---|
| 时间步长 | 1 分钟（60 s）；1440 步时间戳唯一，无重复；`Δ` 恒为 60 s |
| 是否把 kW 直接求和当 kWh | **是（初版评估脚本存在此错误）** |
| 是否使用 `energy_kwh = Σ power_kw × Δt_h` | 修正后为 `Σ power_kw × (1/60)` |
| 重复时间戳/重叠时域/重复累计 | 无；只取基准时刻一条预测（horizon=60），不叠加三时域 |
| 是否仅在 B1/B3 共同可行步比较 | 是，983 步；B1-only=B3-only=0 |
| 不可行/缺失/回退处理 | 不计入能耗累计，单独统计；回退候选不参与能耗比较 |
| 预计功率是否超设备能力 | 单机估计最大 4# 1295.8 kW / 5# 1297.8 kW（≈+3.8% 铭牌 1250，远低于实测最大 ≈1497）；本次新增功率硬上限校验，`power_over_cap_count=0` |

### 1.1 量纲错误与修正

初版对比脚本（临时脚本，未入库）把逐分钟 `power_kw` 直接求和并标记为 kWh，**缺 `× Δt_h`，数值夸大 60 倍**。

| 指标 | 初版（错误） | 修正后 |
|---|---|---|
| B1 “电量” | 2,575,893.8（实为 Σ kW） | **42,931.56 kWh** |
| B3 “电量” | 2,492,850.4（实为 Σ kW） | **41,547.51 kWh** |
| B1 平均功率 | — | **2,620.44 kW** |
| B3 平均功率 | — | **2,535.96 kW** |

校验：2,575,893.8 kW × (1/60) ≈ 42,931.6 kWh；2,492,850.4 × (1/60) ≈ 41,547.5 kWh，与修正值一致。

修正实现：`optimizer.sum_energy_kwh(powers_kw, step_minutes)`，并新增可复现脚本 `analysis/scripts/b3_compare.py` 与指标文件 `analysis/reports/b/optimization_metrics.json`。回归测试 `test_sum_energy_kwh_units`。

## 2. 3.22% 是否成立

**成立。** 节电率是电量比值，步长缩放因子 `1/60` 在分子分母同时出现并约去：

`(E1−E3)/E1 = (Σ P1 − Σ P3)/Σ P1 = 3.224%`

即无论用“Σ kW”还是“kWh”，比值不变。修正只影响绝对电量口径，不影响 3.224%。

## 3. 983 可行步统计与累计口径

- 时间范围：2026-09-01 00:00 ~ 23:59；总步 1440（1 分钟）。
- 共同可行：983 步（=16.38 h）；B1-only=0，B3-only=0。
- 不可行：457 步，原因均为 `INFEASIBLE_CAPACITY`（预测上界 + 5% 额外备用叠加），与 B1 一致。
- 累计口径：`E = Σ_{公共可行步} power_kw × (1/60)`；不可行/缺失/回退不计入。
- 各机功率/流量均落在 `[validated_min, validated_operating_max]` 内，无越界。

## 4. 42.9% 单机场景差异的原因

场景：reserve=none，需求 upper=200，双机初始运行；两策略**约束完全相同**。

| 项 | B1 规则基线 | B3 优化器 |
|---|---|---|
| 初始状态 | 4#/5# 均运行 | 同 |
| 最终组合 | 4# + 5# | **仅 4#** |
| 各机流量 | 4# 120, 5# 120 | 4# 200, 5# 0 |
| 备用策略 | none | none（相同） |
| 最短运行/停机 | 无锁定 | 无锁定（相同） |
| 启停/切换代价 | 0（不切） | 1 次（停 5#） |
| 功率模型输入/输出 | 4#@120→920.34 kW, 5#@120→1119.33 kW（合计 2039.67） | 4#@200→1165.63 kW |
| 是否允许完成切换 | — | 是（5# 无锁定可停，切换后仍满足供气） |

**根因**：B1 规则“运行机组优先保持”——压力 0.60 处于合格带内时不做任何动作，因此保留双机各 120 m3/min（超供 240 vs 需求 200）；B3 做容量感知的组合选择，停 5# 并以 4# 单机 200 m3/min 满足需求。

这是**决策策略差异**（同一约束下 B1 保守保持 vs B3 组合优化），**不是**不同约束条件造成的差异，也不代表现场节能。默认备用（extra 5%）下该切机不被允许，故 983 步对比中两者均为双机。

B1 固定启停优先级来源：`configs/baseline.yaml` 的 `priority.start_priority=[5#,4#]`、`stop_priority=[4#,5#]`，标记为 `inferred`（依据运行占比与 IGV 调节行为）；未发现实现错误，**保持基线不变**。

## 5. 优化求解正确性

### 5.1 贪心对照网格搜索

在两机可行域上以 600 点网格穷举流量分配，与边际贪心对比：

| 需求(m3/min) | 贪心(kW) | 网格最优(kW) | 差(kW) |
|---|---|---|---|
| 200* | 2039.67 | 2039.67（两机均取下限，超供） | 0 |
| 300 | 2111.57 | 2111.57 | 0 |
| 400 | 2252.12 | 2252.57 | −0.45 |
| 500 | 2558.74 | 2559.11 | −0.37 |

贪心不劣于离散网格（差异为网格分辨率导致，贪心落在顶点更优）。回归测试 `test_greedy_matches_grid_optimum`。

### 5.2 适用条件与最优性

- 选中模型为**多变量线性**，运行时非流量特征用训练均值填充 ⇒ 单机功率对流量**线性**（边际成本恒定）。
- 在可分、对流量线性的目标 + 箱约束 + 等和约束下，**边际贪心即为最优**（等价 LP 顶点解）。
- 局限：若将来改用分段/二次等**非凸**功率函数或引入随流量变化的耦合特征，贪心可能次优；届时需切换为分段 LP/凸求解并加对照测试。

### 5.3 其他正确性检查

- 分段/多变量函数使用：`performance.predict_model` 按 `type` 分派；本次选中 `multivar`。
- 健康/RUL 惩罚**不随流量变化**（按机组常数量），只影响组合选择，不影响流量分配——符合设计，已在测试中确认。
- 组合目标比较：`optimizer.evaluate_combo` 对四组合分别算归一化分项；测试 `test_objective_terms_normalized_and_reported`。
- 硬约束：需求/容量/备用/加载率/锁定/功率上限均由构造或筛选保证；新增 `power_cap_kw_per_device=1600`（工程假设，基于实测最大 ≈1497 取裕度），超限候选判不可行并给 `POWER_OVER_CAP`。

## 6. 修改文件清单

| 类型 | 文件 | 变更 |
|---|---|---|
| 修改 | `src/air_station/optimization/contracts.py` | 新增原因码 `POWER_OVER_CAP` |
| 修改 | `src/air_station/optimization/optimizer.py` | 新增 `sum_energy_kwh()`；组合评估新增单机功率硬上限校验 |
| 修改 | `src/air_station/optimization/service.py` | 不可行时聚合候选原因码（含 `POWER_OVER_CAP`） |
| 修改 | `configs/optimization.yaml` | 新增 `dispatch.power_cap_kw_per_device`、`dispatch.step_minutes` |
| 新增 | `analysis/scripts/b3_compare.py` | 修正后的可复现对比评估脚本 |
| 新增 | `analysis/reports/b/optimization_metrics.json` | 修正后指标（含步长/单位/公共可行步） |
| 修改 | `analysis/reports/b/optimization_report.md` | 电量口径与数值修正、42.9% 说明 |
| 新增 | `analysis/reports/b/B3_validation_audit.md` | 本审计报告 |
| 测试 | `tests/optimization/test_optimizer.py` | 新增 `test_sum_energy_kwh_units`、`test_power_cap_marks_infeasible`、`test_greedy_matches_grid_optimum` |

未修改 A 的代码/数据/模型；未改动 B1/B2 既有接口语义。

## 7. 联合测试真实结果

```
python3 -m pytest tests/optimization -q -rs                -> 67 passed, 0 failed, 0 skipped
python3 -m pytest tests/data tests/models tests/baseline tests/optimization -q -rs -> 145 passed, 0 failed, 0 skipped
```

## 8. 结论

- 电量单位错误**已定位并修复**（夸大 60 倍 → 修正为 kWh）；节电率 **3.224% 仍成立**。
- 42.9% 单机差异为**策略差异**（同约束），已在报告明确，不作为约束差异或现场节能。
- 贪心方法在当前线性功率模型下为最优，已网格对照并加回归测试。
- 新增功率硬上限校验，`POWER_OVER_CAP` 原因码与测试齐备。

**建议：B3 可最终验收，具备进入 B4 的条件**（B4 喘振代理接口需求见 `optimization_report.md` §13）。
