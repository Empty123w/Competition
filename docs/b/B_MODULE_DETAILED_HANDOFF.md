# 空压站项目协作与 B 模块详细交接说明

> 团队仓库（主）：`https://github.com/Wxyun113/Competition.git`
> B 交付 Fork：`https://github.com/yang466/Competition.git`（分支 `feat/b-final-delivery`）
> 团队规模：4 人（A、B、C、D）；协作方式：GitHub Collaborators + 独立分支 + Pull Request
> **当前团队 main 基线：`a28e580`（= Merge pull request #1 from `yang466/feat/b-final-delivery`）**
> B 交付 commit：`736858d`（已并入 main）
> 编写日期：2026-10-20

---

## 1. 文档目的

本文件用于：

1. 说明当前项目真实状态与已完成的 **B 模块（B0–B7）**；
2. 指导 C、D 获取并运行 B 模块，独立完成 MCP/Agent 集成与前端演示；
3. 明确 B 向 C、D 提供的数据、函数与接口契约；
4. 规定 GitHub 协作方式，避免互相覆盖代码；
5. 明确已知数据限制、工程假设、禁止的错误表述与交接关闭条件。

所有成员应先阅读本文件，再改动与 B 相关的代码。B 模块**已冻结**（除明确标注的待确认项外），修改需走公共接口变更流程。

---

## 2. B0–B7 完成情况与未完成的外部联调

### 2.1 已完成（B 侧，已并入团队 main `a28e580`）

| 阶段 | 内容 | 证据（路径） | 状态 |
|---|---|---|---|
| B0 | 数据/接口/实验设置、假设登记、时间切分 | `analysis/reports/b/data_fit_report.md`、`analysis/reports/b/assumptions_register.md` | 完成 |
| B1 | 人工规则调度基线（可执行、可回退） | `src/air_station/baseline/rules.py`、`configs/baseline.yaml` | 完成 |
| B2 | 机组功率模型 + 管网压力近似模型 | `src/air_station/optimization/performance.py`、`pressure.py`、`artifacts/models/power_model.json` | 完成（压力为工程近似） |
| B3 | 多机组合优化调度 | `src/air_station/optimization/{optimizer,service,constraints}.py` | 完成 |
| B4 | 喘振风险代理与安全约束/回退 | `src/air_station/safety/{boundaries,surge}.py`、`configs/safety.yaml` | 完成（代理，非厂家真实线） |
| B5 | dev/valid/test 同工况回测 + 全历史 KPI | `analysis/scripts/{multikpi_harness,b5_unified_summary,b5_load_ratio_all}.py` | 完成 |
| B6 | 封装、测试、C 集成准备 | `src/air_station/optimization/{b_api,dispatch_api}.py`、`tests/optimization/test_b01_b20_acceptance.py`、`analysis/reports/b/integration_B_to_C.md` | **B 侧完成** |
| B7 | 两典型场景与 D 材料 | `analysis/scripts/b7_scenarios.py`、`analysis/reports/b/b7/*` | **B 侧完成** |

### 2.2 未完成（外部联调，非 B 侧可闭环）

| 事项 | 归属 | 说明 |
|---|---|---|
| MCP 包装与 API 路径 | C | 把 `b_api` 封装为 MCP 工具 |
| "预测→健康→调度→喘振→安全"协议级联调 | C | 需 A 的预测/健康接口 + B 的调度/喘振 |
| 端到端 Agent 时延 ≤60s | C | B 算法侧毫秒级；端到端需 C 实测 |
| PLC 控制链路与放行 | C | B 只输出仿真候选，**无写指令路径** |
| 前端重放两场景与图表 | D | 数据已备（见 §15） |
| 官方能效公式/人工基线/加载率定义/压力合格带 | 官方 | B 无法自行判定 |
| 独立用气需求、储气容积、放空气量、真实喘振标签 | A/现场 | 压力与喘振真实验收所需 |

---

## 3. B 组负责什么，与 A/C/D 如何分工

- **B 组**：把 A 的**预测**与**健康**结果，转换为**可解释、可检验、满足安全约束**的机组调度方案，并评估**喘振与供气风险**；向 C 交付契约化函数，向 D 交付带来源标签的图表数据与两个场景。
- **A 组**：数据治理、负荷预测、健康/RUL；向 B 交付滚动校准预测与健康序列（`artifacts/handoff_b/`）。
- **C 组**：Agent 编排、MCP 服务、控制指令。
- **D 组**：前端可视化与比赛材料。

| 角色 | 主要目录（B 相关） |
|---|---|
| A | `data/`、`src/air_station/models/`、`artifacts/handoff_b/`、`analysis/reports/A_to_*` |
| **B** | `src/air_station/{baseline,optimization,safety,simulation}/`、`configs/{baseline,optimization,safety,pressure_mpc}.yaml`、`tests/{baseline,optimization,safety,simulation,b5}/`、`analysis/scripts/*`、`analysis/reports/b/`、`docs/b/` |
| C | `src/air_station/{api,mcp_servers,agent,control,db}/`、`deployment/`、`tests/{api,mcp}/` |
| D | `frontend/`、`demo/`、`docs/`（B 之外）、`tests/e2e/` |

---

## 4. 项目目录、重要代码文件及核心功能

```text
configs/                      B 参数与约束
  baseline.yaml               B1 规则/约束/优先级/压力带(示例)
  optimization.yaml           B2 模型/B3 目标权重/时间切分
  safety.yaml                 B4 喘振边界配置与阈值
  pressure_mpc.yaml           压力 MPC 参数(研究)
src/air_station/
  baseline/
    rules.py                  B1 人工规则调度
    evaluator.py pipeline.py  数据读取/快照构造
  optimization/
    performance.py            B2 功率模型(estimate_power/train_performance)
    pressure.py               B2 压力近似模型
    optimizer.py              B3 组合枚举与选优(search_combinations)
    service.py                B3 入口(optimize_dispatch/optimize_json)
    constraints.py            约束/可用容量/过渡可行性
    unit_commitment.py        bal / LRB 负荷分配(UCConfig)
    fallback.py               安全回退(B1 规则 + 复检)
    contracts.py              数据契约与错误码
    forecast_adapter.py       A6 预测接入
    dispatch_api.py           B6 统一三模式接口(plan_dispatch)
    b_api.py                  B6 稳定导出(optimize_dispatch/assess_surge_risk/plan_dispatch)
    selective_balanced.py    选择性均衡(研究)
    energy_aware_balanced.py 能量感知均衡(研究)
  safety/
    boundaries.py surge.py    B4 代理边界与喘振评估
  simulation/
    closed_loop.py            B7 闭环压力仿真
analysis/scripts/             回测/实验/场景脚本
analysis/reports/b/           结果与报告
docs/b/                       01..08 八份文档
```

**模块依赖**：`python>=3.11`、`pandas numpy pyyaml scikit-learn lightgbm joblib`；模型 `artifacts/models/{power_model,pressure_model,surge_boundary}.json`；配置 `configs/*.yaml`。

---

## 5. 如何克隆仓库、安装环境、运行测试

### 5.1 克隆
```bash
git clone https://github.com/Wxyun113/Competition.git
cd Competition
git switch main
git pull --ff-only
git log -3 --oneline        # 应看到 a28e580 (Merge PR #1)
```

### 5.2 环境（Python 3.11+）
```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]" || python -m pip install --break-system-packages pandas numpy pyyaml scikit-learn lightgbm joblib openpyxl pytest
```

### 5.3 检查
```bash
python -c "import air_station; print('air_station import OK')"
python -c "from air_station.optimization import b_api; print(b_api.API_VERSION, b_api.DISPATCH_MODES)"
git ls-files data/processed | head
git ls-files artifacts/models | head
```

### 5.4 运行测试
```bash
python -m pytest tests/optimization tests/safety tests/simulation -q
python -m pytest tests/data tests/models tests/baseline tests/b5 -q
python -m pytest tests/optimization/test_b01_b20_acceptance.py -v
```
期望：**328 passed, 0 failed, 0 skipped**（B 侧开发环境实测，见 §17）。

---

## 6. B1 人工规则调度原理及调用方式

- 入口：`air_station.baseline.rules.decide(snapshot, forecast, health, constraints, config, state) -> DispatchResult`。
- 原理（`configs/baseline.yaml`）：
  1. 需求取预测**上界**（`reserve.basis: upper_bound`）；
  2. `start_priority=["5#","4#"]`，保证容量 ≥ 需求 + 备用；
  3. `load_allocation`: 4# 基载、5# 调节；
  4. 压力低于 `hard_lo_mpa` 强制加机；去抖 `confirm_minutes`、`hysteresis_mpa`。
- 角色：**公平对照基线**；优化失败时作为**安全回退**（`fallback.rule_fallback`）。
- 调用示例：
```python
from air_station.baseline import rules as B1
from air_station.optimization.contracts import PolicyState
cfg = B1.load_config("configs/baseline.yaml")
r = B1.decide(snapshot, forecast, [], constraints, cfg, PolicyState())
```

---

## 7. B2 功率与压力模型的实现及工程假设

### 7.1 功率模型
- 训练：`performance.train_performance()` → `artifacts/models/power_model.json`（`model_version=b2-power-v0.1`）。
- 拟合样本：运行且流量∈[120, 268.9]；候选 `linear/poly2/piecewise/multivar`，按 **MAE** 选优。
- 估计：`PERF.estimate_power(device_id, flow, *, discharge_pressure_mpa, igv_percent, ambient_temp_c, model_path)`。
  - `flow=0`→功率 0；非法→`INVALID_INPUT`；缺模型→`MODEL_UNAVAILABLE`。
- 数值例子（4#）：Q=120→920.3 kW（SEC 0.1278 kWh/m³）；Q=240→1288.3 kW（SEC 0.0895）。**SEC 随流量下降**（大马拉小车不经济的根因）。
- **工程假设**：无厂家性能曲线，`validation_mode=simulation`。

### 7.2 压力模型
- `pressure.train_pressure()`、`predict_model`；候选 `persistence/linear_dynamics`。
- 实测 `corr(母管压力, 总流量)≈−0.42`，一步预测**不优于持续性基线** → **反事实预测能力不足**。
- `reference_band_mpa:[0.55,0.66]` 为**待确认示例（CAL-05）**，非厂家安全标准。

---

## 8. B3、bal、LRB-t10 三种调度算法的区别

| 方面 | B3（能效优先） | bal（低负荷优先） | LRB-t10（加载率均衡） |
|---|---|---|---|
| 入口 | `service.optimize_dispatch` | `UC.plan(UCConfig(balanced=True))` | `UC.plan(UCConfig(spread_balance=True, spread_tol=0.10))` |
| 目标 | 最小功率 | 消除可再平衡低负荷 | 约束机组间加载率差 |
| 关键机制 | 枚举 4 种组合 + 最小功率分配 | 需求≥0.5·额定·台数时强制每台≥134.45 | 双机限制 \|f4−f5\|≤0.10×268.9，容差内取最小功率 |
| policy_version | `b3-optimizer-v0.1` | `balanced-uc-v0.1` | `load-balanced-lrb-v0.1` |
| 全历史 service_equiv SEC 降% | 7.71 | 7.50 | **6.39** |
| 全历史大马拉小车降%（paired） | 48.3 | **77.2** | 61.8 |
| D3 加载率达标率 | 0–11% | 2–30% | **≈100%** |

**共同硬约束**：容量 + 5% extra 备用、最低稳定流量 120、爬坡、最短开/停机 15min、最大切换 2/h、过渡供气连续、压力安全、喘振。**目标函数**（`configs/optimization.yaml: dispatch.weights`）：`w_power·功率 + w_low·低负荷 + w_switch·切换`，权值可配置。
**推荐**：主方案 **LRB-t10**（三项同时达标，内部口径）；备选 bal / B3。

---

## 9. B4 喘振风险评估、安全约束与回退

- 入口：`SG.assess_surge_risk(snapshot, candidate, *, boundary_version, config_path, boundary_path) -> SurgeAssessment`。
- 边界：`artifacts/models/surge_boundary.json`（历史运行点包络，`boundary_type=proxy`，**非厂家真实喘振线**）。
- 裕度：`margin=(planned_flow − boundary_flow(pressure))/boundary_flow`；`margin<0`→**high**；`[0,0.10)`→**medium**；`≥0.10`→**low**；关键值缺失/超域→**unknown**（**不得默认 low**）。
- 阻断：`block_on_high/block_on_unknown=true` → `safety_blocked=true`（不自动执行）。
- 回退：任何调度失败/超时 → `fallback.rule_fallback`（B1 + 复检），`is_fallback=true`，`execution_permission="simulation_candidate_only"`。

---

## 10. B5 dev/valid/test 回测、完整 KPI 与可信度限制

### 10.1 分段与口径
| 段 | 区间 | 全步 | paired_exec | service_equiv |
|---|---|---|---|---|
| dev | 2026-03-26~07-31 | 12284 | 7600 | 6957 |
| valid | 08-01~08-20 | 1916 | 1914 | 1857 |
| test | 08-21~09-12 | 2113 | 2063 | 1979 |

- `paired_exec`：各策略均 `status=success & exec=True`；`service_equiv`：再要求 `flow ≥ obs−0.05`。**full_window 各策略供气步不同步，不可直接比。**

### 10.2 全历史统一汇总（分段求和后重算，不平均百分比）
| 策略 | service_equiv SEC 降% | 大马拉小车降%（paired） | D3 达标率 |
|---|---|---|---|
| B3 | 7.71 | 48.3 | 0–11% |
| bal | 7.50 | 77.2 | 2–30% |
| **LRB-t10** | **6.39** | 61.8 | **≈100%** |

**valid/test 单段服务等价 SEC 为 3.57% / 3.04%（<6%），不省略。**

### 10.3 可信度限制
- 官方**未给**能效公式、人工基线、加载率数学定义、压力合格带 → **内部口径，非官方得分**。
- 供气差额为**历史供气代理差额**（A6 目标=供气总量，非独立实测需求）。
- 加载率 D1（相对额定）**结构性不可行**（可行需求∈[245,255]∪[485,510]）；D3 非官方定义。

---

## 11. 闭环压力仿真及五种工况

- 代码：`src/air_station/simulation/closed_loop.py`；脚本：`analysis/scripts/sim_closed_loop_study.py`；数据：`analysis/reports/b/simulation/{sim_closed_loop.json,sim_ledger.csv,sim_sensitivity.json}`。
- 组成：独立模拟用气需求、储气等效容积 `K_VOL`、泄漏、质量平衡压力动态、BOV 放空、压力 **PI**、执行延迟、预测噪声、设备故障。
- **显式记账**：安全泄压 `safety_vent_m3`、未满足缺口 `unserved_deficit_m3`、钳制步；质量守恒 `净入流 − 储气变化 − 泄漏 − 泄压 + 未满足 = 0`（残差≈0）；硬钳制 [0.40,0.75] **在合格带外**。

| 场景 | 压力合格率 | 压力std | 需求满足度 | 缺供m³ | 放空/未满足m³ | 回退步 | 判定 |
|---|---|---|---|---|---|---|---|
| normal | **1.000** | 0.0061 | 0.987 | 7,896 | 0/0 | 0 | 达标 |
| low | **1.000** | 0.0039 | 0.988 | 4,263 | 0/0 | 0 | 达标（std↓~89%） |
| step | 0.622 | 0.0920 | 0.971 | 16,398 | 0/6,842 | 122 | 物理容量不足 |
| peak | 0.264 | 0.0919 | 0.943 | 41,415 | 0/31,289 | 224 | 物理容量不足 |
| fault | 0.667 | 0.0864 | 0.931 | 42,521 | 8,056/31,291 | 143 | 单机失效容量不足 |

**限制**：全部为**工程假设下的模拟验证**，**不等同现场 99.5% 达标**；未放宽合格带、未裁剪压力、未编造实测数据。

---

## 12. B6 的正式 Python 接口、输入输出字段和错误码

### 12.1 稳定导出
```python
from air_station.optimization import b_api
b_api.API_VERSION        # "b-api-v1.0"
b_api.DISPATCH_MODES     # ("b3","balanced","load_balanced")
b_api.POLICY_VERSIONS    # {b3,balanced,load_balanced} 对应版本
b_api.plan_dispatch      # 统一三模式
b_api.optimize_dispatch  # B3（原任务书函数名）
b_api.assess_surge_risk  # B4（原任务书函数名）
```

### 12.2 函数签名（真实）
```python
plan_dispatch(mode, snapshot, forecast, health, constraints, *,
              state=None, forecast_seq=None, config_path="configs/optimization.yaml",
              power_model_path=None, baseline_config_path=None, timeout_ms=None) -> dict
optimize_dispatch(snapshot, forecast, health, constraints, *, policy_version="b3-optimizer-v0.1",
                  rul=None, state=None, config_path="configs/optimization.yaml",
                  power_model_path=None, pressure_model_path=None, baseline_config_path=None) -> DispatchResult
assess_surge_risk(snapshot, candidate, *, boundary_version=None,
                  config_path="configs/safety.yaml",
                  boundary_path="artifacts/models/surge_boundary.json") -> SurgeAssessment
```

### 12.3 输出字段（`plan_dispatch` 返回 dict 节选）
`mode, status, is_fallback, execution_permission, as_of, required_flow_m3_min, target_combination,`
`device_actions[{device_id, action, target_flow_m3_min, load_ratio, running_after, estimated_power_kw}],`
`total_flow_m3_min, estimated_power_kw, estimated_energy_kwh, reserve_m3_min, transition_plan,`
`surge_status, surge_assessment, safety_blocked, reason_codes[], constraints[], latency_ms`。

### 12.4 错误码（`optimization/contracts.py`）
`INVALID_INPUT / STALE_FORECAST / INFEASIBLE_CAPACITY / INSUFFICIENT_DATA / MODEL_UNAVAILABLE / FORECAST_MISSING_HOLD / HEALTH_DERATE / HEALTH_STOP_REVIEW / RESERVE_VIOLATION / MIN_RUN_LOCK / MIN_STOP_LOCK / MAX_SWITCH_LOCK / TRANSITION_CAPACITY_GAP / TIMEOUT / SURGE_HIGH_BLOCKED / SURGE_UNKNOWN / SURGE_BOUNDARY_UNKNOWN / PRESSURE_COUNTERFACTUAL_UNSUPPORTED / RUL_SIMULATION_PENALTY_ON`。

---

## 13. A→B→C 的完整数据与函数调用链

```text
A 侧
  forecast（artifacts/handoff_b/*_calibrated.parquet / forecast_json）  ─┐
  health（artifacts/metrics/health_history.parquet / health_json）      ─┤
                                                                        ▼
B 侧
  forecast_adapter.load_forecast_bundle → ForecastResult（as_of/预测/上界）
  pipeline.snapshot_from_frames → StationSnapshot（as_of/母管压力/逐机状态）
  constraints.DispatchConstraints.from_config(baseline.yaml)
  b_api.plan_dispatch(mode, snapshot, forecast, health, constraints)
     ├─ b3        → service.optimize_dispatch → optimizer.search_combinations → constraints
     ├─ balanced  → unit_commitment.plan(balanced=True)
     └─ load_balanced → unit_commitment.plan(spread_balance=True)
        （失败/超时 → fallback.rule_fallback → B1）
  safety.surge.assess_surge_risk(snapshot, candidate) → 风险/裕度/阻断
        ▼
C 侧
  Agent 编排 → MCP 工具（调度 / 喘振）→ 解释 →（经放行后）PLC/SCADA
```

---

## 14. B 向 C 交付什么、C 如何完成 MCP/Agent 联调

**B 交付**：`b_api`（三模式 + 喘振函数）、`dispatch_api.py`、`integration_B_to_C.md`、`C_INTERFACE.md`、`c_call_examples.json`（三模式 + 异常样例）、`tests/optimization/test_dispatch_api.py`、`test_b01_b20_acceptance.py`。

**C 需完成（未完成）**：
1. 在 C 容器 `import air_station.optimization.b_api`，加载 `configs/*` 与 `artifacts/models/*`；
2. 将 `plan_dispatch` / `assess_surge_risk` 封装为 MCP 工具（工具名自定，字段不改）；
3. 跑一次"预测→健康→调度→喘振→安全"协议级联调，确认 `safety_blocked` 能被 C 阻断；
4. 实测端到端时延（B 算法侧 p50 ≤41ms，max ≤0.53s；**端到端 ≤60s 需 C 实测**）；
5. 确认无任何 PLC 写指令由 B 侧发出（`execution_permission="simulation_candidate_only"`）。

---

## 15. B 向 D 交付什么、D 如何完成两个演示场景

**数据与脚本**：
| 用途 | 路径 | 关键字段 | 来源 |
|---|---|---|---|
| 场景一逐点对比 | `analysis/reports/b/b7/scenario1_load_drop_ledger.csv` | t,demand,`{s}_{f4,f5,flow,power,lr4,lr5,low4,low5,on4,on5}` | real_history+model_estimate |
| 场景一 A 预测 | `analysis/reports/b/b7/scenario1_forecast.csv` | as_of,target_time,horizon,predicted/lower/upper | A 预测 |
| 场景一汇总 | `analysis/reports/b/b7/scenario1_summary.json` | energy/supply/sec/low + 降幅 | real_history |
| 场景二事件 | `analysis/reports/b/b7/scenario2_events.json` | status/combo/device_actions/surge/reason_codes | simulation_fixture+proxy |
| 闭环 5 场景 | `analysis/reports/b/simulation/sim_ledger.csv` | scenario,strategy,k,demand,supply,blow,pressure,power_kw,... | simulation |

**D 需完成**：接入 B 输出 JSON 字段；重放两个场景；正确展示"无解/高危阻断"；三成员使用同一指标文件（见 `docs/b/06_B_TO_D_HANDOFF.md`）。

---

## 16. 典型调用示例、异常处理及安全阻断

```python
import pandas as pd
from air_station.baseline import pipeline as PIPE, rules as B1
from air_station.optimization import b_api, contracts as C
from air_station.optimization.forecast_adapter import load_forecast_bundle

dev, st = PIPE.load_processed()
scor = pd.read_parquet("artifacts/handoff_b/forecast_scoring_actuals_calibrated.parquet")
b = load_forecast_bundle("artifacts/handoff_b/forecast_validation_dispatch_calibrated.parquet",
                         scoring=scor, scoring_split="valid")
t = next(t for t in sorted(b.by_issue) if all(h in b.by_issue[t] for h in (15,30,45,60)))
snap = PIPE.snapshot_from_frames(dev, st, t)
con = C.DispatchConstraints.from_config(B1.load_config("configs/baseline.yaml"))
fc = b.forecast_result(t, 15)

out = b_api.plan_dispatch("load_balanced", snap, fc, [], con)   # 三模式：b3/balanced/load_balanced
print(out["status"], out["target_combination"], out["estimated_power_kw"], out["surge_status"])
```
**异常/阻断**（实侧见 `analysis/reports/b/delivery/c_call_examples.json`）：
- 非法 mode → `ValueError`；
- 预测缺失 → `status="INSUFFICIENT_DATA"`, `is_fallback=true`；
- `timeout_ms<=0` → `status="timeout"`, `is_fallback=true`；
- 高喘振 → `safety_blocked=true`（`neg_high_surge`）；
- 容量不足 → `INFEASIBLE`（`neg_capacity_insufficient`）。
**B 绝不在失败时返回伪装成功的控制动作。**

---

## 17. 已有测试结果及 C/D 接收后必须执行的测试

### 17.1 已有（B 侧开发环境）
```text
python -m pytest tests -q   →  328 passed, 0 failed, 0 skipped
（含 B01–B20 20 项、仿真 16 项、三模式集成 6 项）
```
> 接收成员须在自己环境**重新运行**，不得只引用此历史结果作为验收。

### 17.2 C 必跑
```bash
python -m pytest tests/optimization/test_dispatch_api.py tests/optimization/test_b01_b20_acceptance.py -q
python analysis/scripts/c_call_examples.py         # 三模式 + 异常/回退样例
```
并完成 §14 的 MCP/协议联调与端到端时延实测。

### 17.3 D 必跑
```bash
python analysis/scripts/b7_scenarios.py            # 生成两场景数据
git ls-files analysis/reports/b/b7                # 应含 ledger/forecast/summary/events
```

---

## 18. GitHub 协作、分支和 PR 管理

- 禁止直接在 `main` 并行修改；每人固定功能分支（B：`feat/b-optimization*`；本次交付：`feat/b-final-delivery`）。
- 开始工作前 `git switch main && git pull --ff-only && git switch <分支> && git rebase main`（或 `merge`，勿混用）。
- 提交前 `git status`/`git diff`；**只 add 自己负责的路径**，避免 `git add .`。
- PR 模板：完成内容 / 修改范围 / 接口影响 / 测试结果 / 待确认。
- **公共文件**（`pyproject.toml`、`.gitignore`、`configs/`、A 的接口文档、`data/processed/*.parquet`）变更需相关成员共同确认并单独 PR。
- 只有仓库所有者或指定集成人合并 `main`；禁止 force push、禁止删除 `main`。

**本次交付事实**：B 交付分支 `feat/b-final-delivery`（commit `736858d`）已通过 **PR #1** 合并到团队 main（`a28e580`）；Fork `yang466/Competition` 保留同名分支。

---

## 19. 已知数据限制、参数假设和禁止错误表述

**数据限制**：无厂家性能/喘振曲线；无独立工厂用气需求、储气容积、放空气量；无真实喘振标签；历史仅 1 分钟分辨率；健康为无监督弱验证。

**参数假设（source=pending/engineering）**：CAL-02 需求口径、CAL-03 备用（5% extra）、CAL-04 加载率目标、CAL-05 压力带 [0.55,0.66]、AS-02 最低稳定流量 120。

**禁止的错误表述**：
- 不得把内部 SEC/加载率口径称为"官方得分"；
- 不得把压力闭环仿真合格率称为"现场 99.5% 达标"；
- 不得把代理喘振边界画成"厂家真实喘振曲线"；
- 不得把 LRB 数字写成 B3 数字，或反之；
- 不得把少供气计入节能；
- 不得把尚未完成的 C/D 联调写成已完成。

---

## 20. C/D 交接验收清单、待确认事项及交接关闭条件

### 20.1 C 确认
- [ ] 可 `import air_station.optimization.b_api` 并加载配置/模型
- [ ] 三模式 `plan_dispatch` 调用成功（正常/异常/超时）
- [ ] `assess_surge_risk` 返回风险等级/裕度/阻断
- [ ] MCP 工具名与 API 路径已确认（C 回复）
- [ ] 端到端时延实测并记录
- [ ] 确认无 PLC 写指令路径

### 20.2 D 确认
- [ ] 已获得两场景数据（`analysis/reports/b/b7/*`）与闭环数据（`simulation/sim_ledger.csv`）
- [ ] 前端字段与 B 输出一致
- [ ] 两场景可完整重放，含"无解/高危阻断"展示
- [ ] 指标来源标注（real_history/model_estimate/simulation/proxy）

### 20.3 待确认事项（需回复）
```text
C：三模式是否调用成功：是/否
C：喘振阻断是否可被 C 拦截：是/否
C：MCP 工具名：___________ ； API 路径：___________
C：端到端时延实测：___________ 秒
D：两场景是否可重放：是/否 ； 需要补充字段：___________
官方/A：能效公式与人工基线、加载率定义、压力合格带、独立用气需求/储气参数/真实喘振标签
```

### 20.4 交接关闭条件
1. C 三模式调用成功且完成协议级联调；
2. C 冻结端到端时延并对齐"仿真候选"权限；
3. D 两场景可独立重放且字段一致；
4. 接收环境测试通过（≥ 与 §17.1 一致，且无大量 skip）；
5. 公共接口变更均走 PR 审查；
6. 官方待确认项有明确答复或明确标注"不可验证"。

满足后 B 模块进入冻结维护状态；修改公共接口/字段须由受影响成员共同确认。

---

## 附：B 模块文档与证据索引
- 八份文档：`docs/b/01_B_GROUP_WORK_REPORT.md` … `docs/b/08_B0_B7_ACCEPTANCE_MATRIX.md`
- 交付包：`analysis/reports/b/delivery/{B_DELIVERY_README,ALGORITHMS_AND_API,DEMO_SCENARIOS,RUNBOOK,OPEN_ITEMS}.md`、`c_call_examples.json`、`load_ratio_all.json`
- KPI/实验：`analysis/reports/b/{B5_UNIFIED_SUMMARY,B_KPI_FINAL_ACCEPTANCE,SIM_CLOSED_LOOP_STUDY,MAIN_STRATEGY_RECOMMENDATION}.md`、`b5_unified_summary.json`
- 集成：`analysis/reports/b/{C_INTERFACE.md,integration_B_to_C.md}`
- 场景：`analysis/reports/b/b7/*`（B7）；`analysis/reports/b/simulation/*`（闭环）
