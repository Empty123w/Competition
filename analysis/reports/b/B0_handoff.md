# B0 阶段交付与交接说明

- 阶段：B0（数据适配、接口验证、开发规划）
- 负责人：成员 B
- 分支：`feat/b-optimization`
- 日期：2026-10-18
- 依据任务书：《B成员人工基线调度优化与喘振预警详细任务书》V1.1（已同步至 `docs/`）
- 代码基线：提交 `471fadd`
- 状态：B0 完成并按 V1.1 §4 复核，等待确认后进入 B1

---

## 1. B0 完成情况

| 交付项 | 状态 | 产物/证据 |
|---|---|---|
| 项目与文档通读 | 完成 | README、pyproject、`A_to_B_数据接口.md`、`HANDOFF.md`、`标准字段清单.md`、`A_to_C_服务接口.md` |
| Git 与环境核查 | 完成 | 分支 `feat/b-optimization`，工作区干净；Python 3.11.2，依赖已装 |
| 现有测试真实运行 | 完成 | `python -m pytest tests/data tests/models -q` → **47 passed，0 skipped** |
| 标准数据核查 | 完成 | 见 `data_fit_report.md` §3~§4 |
| 预测接口验证 | 完成 | `forecast_json()` 返回 15/30/60 分钟预测，实测通过 |
| 健康接口验证 | 完成 | `health_json()` 返回 4#/5# 风险等级，实测通过 |
| 历史回测对齐验证 | 完成 | `actual_flow == station@(time+h)`，最大差 0 |
| 假设登记 | 完成 | `assumptions_register.md`（AS-01~29、CAL-01~06、CQ-01~08） |
| V1.1 任务书同步 | 完成 | 快进合并至 `471fadd`，保留 B0 报告 |
| V1.1 §4 复核 | 完成 | `data_fit_report.md` §3.1、§7~§12 补齐快照哈希、时间切分、场景清单、验收对照 |
| B 模块目录与接口规划 | 完成 | 见本文 §4、§5（对齐 V1.1 §3.1、§3.3） |
| B1 文件级实施方案 | 完成 | `B1_implementation_plan.md` |

**本次未修改** A 的 `data/`、`artifacts/`、`configs/`、源码；未推送、未合并。
**同步方式**：`git merge --ff-only origin/feat/b-optimization`，未使用强制重置，未覆盖 B0 报告。

## 2. 关键验证结论

1. 数据完备：264,961 行 × 1 分钟，2026-03-12~09-12，4#/5# 各 264,961 行。
2. 运行特征清晰：4# 运行占比 82.4%（基载，IGV 常 100%），5# 运行占比 97.0%（调峰，IGV 可下探约 34%）。
3. 存在**超额运行**：运行中 `load_ratio>1` 约占 12%，最高 111.5% 额定流量。
4. 存在**低流量低效区**：比功率在 230 m3/min 段约 5.46，275 m3/min 段约 5.11。
5. 两个运行时接口均可用；在线预测仅支持数据末端之后的时域，历史回测须用 `forecast_test_predictions.parquet`。
6. 数据异常已登记（D1~D7），建模前需按掩码剔除运行但流量为 0、比功率离群、压力为 0 的样本。

## 3. 阻塞问题

| 编号 | 阻塞项 | 影响 | 需要的输入 | 当前缓解 |
|---|---|---|---|---|
| BL-01 | 无厂家喘振边界曲线/最低稳定流量 | 安全模块无法用真实边界 | CQ-06 | 以 `simulation` 单调曲线假设（AS-01~04）先开发，输出带 `simulation` 标记 |
| BL-02 | 无母管压力合同/目标带 | 压力约束取值不确定 | CQ-01 | 用实测分布区间先取 AS-06~08 |
| BL-03 | 无启停时间、最小运行时长、启停成本、攀升速率 | 调度动态约束与切换成本不确定 | CQ-04、CQ-05 | 以工程假设占位（AS-22~26），回测做敏感性分析 |
| BL-04 | 健康风险→限载系数未定 | 优化可行域边界不确定 | CQ-03 | 以工程假设（AS-19）先实现，配置可覆盖 |
| BL-05 | 供气备用系数未定 | 安全裕度取值不确定 | CQ-02 | 以 5%~10% 假设占位（AS-11） |
| BL-06 | 电价/能耗口径未定 | 节能表述缺少统一口径 | CQ-07 | 回测先以 kWh、kWh/m3 报告，不使用金额 |

> 以上阻塞均不阻断 B1（人工基线）开发：基线还原只依赖真实运行数据，不依赖安全边界。

## 4. B 模块目录规划（对齐 V1.1 §3.1）

> 采用 V1.1 建议的 `baseline/optimization/safety`（与 A 交接说明一致）；若 C 已有公共契约，先与 C 对齐再定最终包名。B1 仅建立 `baseline/` 与其测试。

```
src/air_station/
  baseline/        __init__.py rules.py evaluator.py pipeline.py
  optimization/    __init__.py contracts.py performance.py pressure.py
                   constraints.py optimizer.py fallback.py service.py
  safety/          __init__.py boundary.py features.py evaluator.py service.py
configs/           baseline.yaml optimization.yaml safety.yaml evaluation_b.yaml
analysis/reports/b/ data_fit_report.md baseline_report.md optimization_report.md
                   surge_report.md assumptions_register.md
data/fixtures/b/   normal.json load_drop.json health_derate.json infeasible.json
artifacts/metrics/ baseline_metrics.json optimization_metrics.json surge_sim_metrics.json
tests/             baseline/ optimization/ safety/ integration_b/
docs/              integration_B_to_C.md metrics_dictionary_b.md demo_evidence_b.md
```

- 与 B0 早期提案的差异：`optimization` 增加 `contracts.py/pressure.py/fallback.py`，`safety` 改为 `boundary.py/features.py/evaluator.py`，配置名 `evaluation_b.yaml`，测试含 `integration_b/`，并新增 `data/fixtures/b/` 与 `docs/` 交付物。本阶段按 V1.1 命名执行。
- 产物入库规则：B 新增指标 JSON 需对 `.gitignore` 加精确例外或另存 `analysis/reports/b/`，并记录模型/数据哈希（见 `data_fit_report.md` §3.1）。未经 A 确认不修改已入库的 `data/processed/*.parquet`。

## 5. B ↔ C 对接接口（对齐 V1.1 §3.3）

> 命名与数据模型最终由 C 维护的公共契约确认。两函数不得读取全局可变状态或直接访问控制系统，输入输出可 JSON 序列化。C 负责 REST/MCP 包装、独立安全校验、人工确认与模拟下发。

```python
def optimize_dispatch(
    snapshot: StationSnapshot,
    forecast: ForecastResult,
    health: list[HealthAssessment],
    constraints: DispatchConstraints,
    *, policy_version: str,
) -> DispatchResult: ...

def assess_surge_risk(
    snapshot: StationSnapshot,
    candidate: DispatchResult,
    *, boundary_version: str,
) -> SurgeAssessment: ...
```

### 5.1 输入（B 消费 A / C）

| 输入对象 | 字段 | 来源 | 单位 |
|---|---|---|---|
| `ForecastResult` | `predicted_flow/lower_bound/upper_bound`、`horizon_minutes`、`target_time` | `forecast_json()` / 冻结测试表 | m3/min / min / ISO |
| `HealthAssessment` | `device_id`、`risk_level`、`suggestion` | `health_json()` | — |
| `StationSnapshot` | `as_of`、逐机 running/flow/power/pressure/igv/bov | `device_timeseries.parquet` 或 C | — |

B 在输入适配层把 A 的 `device_id="4#"/"5#"`、中文 `risk_level` 转为统一内部枚举，并保留原值与来源；无时区本地 `time` 与 UTC `evaluation_time` 先归一到同一时区再比较。

### 5.2 输出（B 提供给 C）

**`DispatchResult`** 至少包含：

```
status, reason_codes, as_of, forecast_id, health_ids, model_version, policy_version,
validation_mode, objective_components, device_actions[], total_flow_m3_min,
estimated_power_kw, pressure_setpoint_mpa, reserve_m3_min,
constraints[]:{name, pass, margin, unit}, explanation, elapsed_seconds, action_sequence[]
```

- `INFEASIBLE` / `INSUFFICIENT_DATA` / `TIMEOUT` 时不得伪造有效动作清单。
- 数据质量与状态异常用结构化错误：`INVALID_INPUT`、`STALE_FORECAST`、`MODEL_UNAVAILABLE`、`INFEASIBLE_CAPACITY`、`SURGE_BOUNDARY_UNKNOWN`。
- 可附 `fallback_candidate`，但须再次安全校验，不得因求解失败返回上次方案并标成功。

**`SurgeAssessment`** 每台机至少包含：

```
risk_level(low/medium/high/unknown), margin_value, margin_unit, margin_definition,
boundary_source, boundary_version, trigger_factors[], limit_suggestion, validation_mode
```

- 无法评价用 `unknown`，不得把缺曲线自动视为 low；高风险候选标 `safety_blocked=true`。

**基线对照**（B1）：`baseline/` 提供与优化同构的 `DispatchResult`（`policy_version` 标为规则基线），便于 B5 公平对比；命名待 C 契约确认。

### 5.3 尚待确认的对接参数

见 `assumptions_register.md` §1A（CAL-01~06）与 §7（CQ-01~08）：时区、供气需求口径、备用定义、加载率口径、压力合格带、启停与安全阈值均为**待确认**。未冻结前仅用带标记的配置开发验证。

## 6. B1 人工调度基线（对齐 V1.1 §5）

详细文件级方案见 `B1_implementation_plan.md`。要点：

- 文件：`baseline/__init__.py`、`rules.py`（输出开停/各机加载/设定压力）、`evaluator.py`（记录动作原因、启停次数、压力越界、供气不足、能耗、不可行）、`pipeline.py`（历史回放/滚动调用）、`configs/baseline.yaml`。
- 规则：运行机组优先保持；压力低于下限且连续满足时增载/启机；高于上限且连续满足时减载/停机；固定启停优先级（5# 调节、4# 基载）；最小运行/停机时间；动作迟滞；预测缺失时守恒。
- 公平性：基线输入与优化器相同的 `snapshot/forecast/health/constraints`，只用规则、不窥视未来真实负荷；历史实际轨迹另命名 `observed_operation` 并列报告（V1.1 §5.1）。
- 指标：总电耗、SEC（kWh/m3）、压力合格率、超额率、启停次数、供气缺额。
- 测试：≥10 个用例，覆盖阈值附近、启停锁定、容量不足、缺数据、确定性与 JSON 序列化。

## 7. 下一步（等待确认）

1. 确认本报告、`data_fit_report.md`、`assumptions_register.md` 的复核修订；
2. 确认 `B1_implementation_plan.md` 的文件与测试方案；
3. 确认 §3 中可先行决策的参数（CAL-01~06、CQ-01~03 等）；
4. 确认后进入 B1 编码。

**在收到确认前，不执行 Git push、合并或其他远程仓库操作，不提前开发 B2~B7。**

---

## 8. 增量变更记录：模拟 RUL 接口核查（2026-10-18）

### 8.1 核查结论

截至本次核查，**本地工作区与远程 `origin/feat/b-optimization` 均未发现名为 `rul_json()` 的接口或任何 RUL 模块**。因此「核验 `rul_json()` 可调用、输出字段与 `validation_mode=simulation`」一项**无法执行**。

### 8.2 核查范围与证据

| 检查项 | 命令/方法 | 结果 |
|---|---|---|
| 远程新提交 | `git fetch origin feat/b-optimization`；`git log HEAD..origin/...` | 无新提交，HEAD 仍为 `471fadd` |
| 远程分支 | `git branch -r` | 仅 `origin/feat/b-optimization` |
| 接口名搜索 | 全仓库 grep `rul_json` / `RUL` / `remaining_life` | 仅命中历史**局限说明**与预留字段，无实现 |
| 模块导入 | `import air_station.models.health.rul` 等 | `ModuleNotFoundError` |
| 文件搜索 | `find -iname '*rul*'` | 仅 `rules.py`（名称含 "rul" 子串），无 RUL 文件 |
| 测试搜索 | `find tests -name 'test_*.py'` | 无 RUL 相关测试 |

`analysis/reports/phaseA5/health_report.md` 与 `phase1/` 文档明确记载「无 RUL 标签」「RUL 需模拟标签」，说明 A 当前状态是**尚未交付 RUL**，与本地核查一致。

### 8.3 对 B0/B1 的影响

- **B0 不受影响**：B0 数据分析、假设登记、时间切分、场景清单均不依赖 RUL。
- **B1 不受影响**：`baseline/` 与 `contracts.py` 未引用 RUL；B1 规则保持原样，无需改动。
- **现有接口兼容**：复核 `forecast_json()`（`status=success`，15/30/60 分钟）与 `health_json()`（`status=success`，`validation_mode=unsupervised`，设备 4#/5#）均可正常调用。

### 8.4 待办（待 A 提供后再执行）

1. 由 A 将 RUL 接口推送到可获取的分支并说明模块路径与函数签名；
2. B 侧核验：可导入、字段完整、`validation_mode=simulation`、只读不重算；
3. 在 `data_fit_report.md` 追加 RUL 字段说明（新增小节，不改动既有分析）。

### 8.5 安全边界

模拟 RUL 仅作**提示性参考**，B 不得据此自动停机，也不得将其表述为真实设备寿命或真实 RUL 精度。

## 9. B3 可选 RUL 寿命惩罚接入设计（预留，不在 B1 实现）

> 本设计为 B3 预留，B1 不实现；待 A 交付 `rul_json()` 后按其字段落地。核心原则：RUL 为**可选软惩罚**，不得突破供气与安全硬约束。

### 9.1 输入适配

- 新增可选输入 `RulAssessment`（`device_id`、`rul_hours`、`rul_unit`、`confidence`、`validation_mode`、`source`）；
- 通过输入适配层把 A 的 RUL 输出映射为内部对象，保留原值与来源，禁止用最新值回填历史；
- 历史回放缺少逐时刻 RUL 时，RUL 惩罚项**关闭**并在 `source_flags` 标注 `rul: unavailable`。

### 9.2 目标函数中的软惩罚项

- B3 目标：`min 预计电耗 + Σ 软权重`；
- 可选 RUL 惩罚：`w_rul · max(0, (rul_threshold_hours − rul_hours)) / rul_threshold_hours`，按设备配置权重 `w_rul`（默认 0，未确认前不启用）；
- 惩罚只影响**可行域内**的组合排序，**不改变**需求、备用、压力、喘振、启停等硬约束；
- `w_rul=0`（默认）时优化器行为与无 RUL 完全一致，确保可复现与向后兼容。

### 9.3 输出与来源标注

- `DispatchResult.objective_components` 增加分项 `rul_penalty` 与 `rul_weight`；
- `source_flags.rul = simulation`（或 `unavailable`）；
- `validation_mode` 保持 `simulation`；解释文案标注「RUL 为模拟寿命提示，不用于自动停机」。

### 9.4 触发条件（可选，默认关闭）

- 当 `rul_hours` 低于配置阈值时，仅**降低该机调度优先级**或**给出维护建议**；
- **严禁**由 RUL 触发自动停机或硬降容，除非后续由工艺/厂商冻结为已验证策略。
