# B4 喘振风险代理与安全边界报告

- 阶段：B4
- 负责人：成员 B
- 分支：`feat/b-optimization-aligned`
- 日期：2026-10-18
- 依据任务书：V1.1 第 8 节
- 结论口径：**代理评分/统计边界，非厂家真实喘振线**；低风险不等于现场安全认证；无真实喘振正例，不报告真实预警准确率

---

## 1. 新增与修改文件

| 类型 | 文件 |
|---|---|
| 新增 | `src/air_station/safety/__init__.py`、`boundaries.py`、`surge.py` |
| 新增 | `configs/safety.yaml` |
| 新增 | `tests/safety/{__init__,_helpers,test_boundaries,test_surge,test_integration_b3}.py` |
| 新增 | `artifacts/models/surge_boundary.json`（历史包络工件，带版本/来源） |
| 新增 | `analysis/reports/b/surge_risk_report.md`、`surge_sample_high.json`、`surge_sample_unknown.json` |
| 修改 | `src/air_station/optimization/contracts.py`（新增 `SurgeAssessment`、原因码、`DispatchResult.surge_assessment/safety_blocked`） |
| 修改 | `src/air_station/optimization/service.py`（接入 B4 评估，替换 `not_assessed_b4` 占位） |
| 修改 | `configs/optimization.yaml`（`dispatch.surge` 启用与版本） |
| 修改 | `tests/optimization/test_service.py`（喘振集成断言）、样例 JSON（含 `surge_assessment`） |

未修改 A 的代码/数据/模型；B3 公共接口向后兼容（`surge_status` 保持存在，新增可选字段）。

## 2. 风险代理模型与阈值来源

### 2.1 两级边界（`boundaries.py`）

1. **工程假设边界**（默认评估边界，`boundary.source=engineering_assumed`）
   `q_boundary(p) = 90 + 350·(p − 0.66)` m3/min，随压力单调上升；`p ∈ [0.45, 0.75] MPa`。
   取 90 m3/min（低于 `validated_min=120`）作最低稳定流量示意，来源标 `engineering`。
2. **历史运行包络**（`historical_envelope`，证据用）
   按机组 × 压力分箱取流量 2% 分位，仅表示“历史少见的运行点”，来源标 `inferred`，写入 `surge_boundary.json`。

> 数据实测：5# 低流量调节充分（0.60 MPa 分箱 2% 分位 ≈ 119.9 m3/min）；4# 长期基载（同分箱 ≈ 188.8）。历史包络受**使用习惯**影响，不能当作能力边界。

### 2.2 阈值（`configs/safety.yaml`，来源 `engineering`）

| 参数 | 值 | 说明 |
|---|---|---|
| `warn_margin` | 0.10 | margin<0→high；[0,0.10)→medium；≥0.10→low |
| `igv_low_percent` | 35 | IGV 低于此→低负荷区（medium 因子） |
| `bov_open_closed_percent` | 95 | BOV 闭度低于此→放空阀开启（medium 因子） |
| `rapid_load_change_m3min` | 60 | 与当前流量差超过此→快速变负荷（证据，low） |
| 过渡/历史包络因子 | — | 仅记录（low），不单独升级风险 |

`margin_definition = (planned_flow − boundary_flow)/boundary_flow`（`margin_unit="-"`，同一压力坐标系内计算）。

## 3. 四类风险判定

- **low**：margin ≥ warn，且无 medium 级触发因子。
- **medium**：0 ≤ margin < warn，或存在 `igv_low` / `bov_open` 因子。
- **high**：margin < 0（低于代理边界），或基础等级经因子升至 high。
- **unknown**：边界版本不匹配、边界工件缺失、压力缺失（运行中但测量缺失）、压力超出边界有效域。

`high` 与 `unknown` 均触发 `safety_blocked=true`（unknown 不得视为安全通过）；`medium` 不阻断但标记 `requires_field_review`。每条含 `trigger_factors`、`boundary_source`、`boundary_version`、`limit_suggestion`、`validation_mode`、`reason_codes`。

## 4. 与 B3 优化器集成

- `optimize_dispatch` 在生成仿真候选后调用 `assess_surge_risk(snapshot, candidate, *, boundary_version)`。
- 输出：`surge_status`（low/medium/high/unknown）、`surge_assessment`（逐机）、`safety_blocked`；移除占位 `SURGE_NOT_ASSESSED_B4`，按等级加入 `SURGE_HIGH_BLOCKED/SURGE_MEDIUM_REVIEW/SURGE_UNKNOWN`。
- 回退候选同样经 B4 评估，摘要带 `surge_status`/`safety_blocked`。
- 保留 B2 压力反事实不可靠限制；`execution_permission` 仍为 `simulation_candidate_only`，现场放行由 C 独立门禁。
- 未为放行候选降低阈值。

## 5. B3+B4 联合评估

### 5.1 真实窗口（2026-09-01，1440 步，B3 可行 983 步）

| 项 | 结果 |
|---|---|
| 候选风险等级 | **low 983 / medium 0 / high 0 / unknown 0** |
| 逐机等级 | low 1966 |
| 被阻断候选 | **0** |
| 需人工复核 | 983（`requires_field_review=true`，现场放行未确认） |
| 求解耗时 | median 0.0301 s / max 0.1035 s |

两机在 983 步中均运行于 ≈250 m3/min，远高于代理边界（≈62~90），故均为低风险；这不代表现场安全认证。

### 5.2 合成压力测试（构造场景）

| 场景 | 结果 |
|---|---|
| 正常运行/高负荷 | low |
| 低流量（计划 50 < 边界 69@0.60） | **high + safety_blocked** |
| IGV<35 / BOV<95 组合 | medium + `SURGE_MEDIUM_REVIEW` |
| 压力缺失（运行中测量缺失） | **unknown + safety_blocked** |
| 压力超出有效域 / 边界版本不匹配 / 边界工件缺失 | **unknown + safety_blocked** |
| 启动过渡 | 记录 `start_transition` 因子 |

样例：`surge_sample_high.json`（high）、`surge_sample_unknown.json`（unknown）。

### 5.3 对调度候选的影响

- 真实窗口的 B3 候选未被风险过滤改变（均 low），**原 3.224% 仿真估计基准保持不变**；风险过滤后节能未重新计算，不得宣称过滤后仍达 3.224%。
- 当计划进入低流量/异常 IGV/BOV 或数据缺失时，候选被标记 `high`/`unknown` 并阻断，需人工复核。
- `medium` 候选保留但需复核。

## 6. B4 尚未解决的安全不确定性

1. 无厂家喘振曲线与真实喘振正例，代理边界为工程假设，**不能给出真实喘振提前预警时长或准确率**。
2. 历史包络受运行习惯影响，不能代表设备能力。
3. IGV/BOV 取当前测量作证据，目标工况下的实际开度未知。
4. 切换过渡只做容量/启动因子检查，缺动态模型，无法量化瞬态喘振风险。
5. 压力反事实不可靠（B2 限制），压力安全仍未确认。

## 7. 需成员 C 确认的接口字段

- `assess_surge_risk(snapshot, candidate, *, boundary_version, config_path?, boundary_path?) -> SurgeAssessment`。
- `SurgeAssessment`：`status, boundary_version, boundary_source, validation_mode, devices[], safety_blocked, counts{low,medium,high,unknown}, reason_codes[], capability`。
- 逐机字段：`device_id, risk_level, risk_margin, margin_definition, planned_flow_m3min, boundary_flow_m3min, pressure_mpa, historical_envelope_m3min, trigger_factors[], boundary_source, boundary_version, limit_suggestion, validation_mode, reason_codes[]`。
- `DispatchResult` 新增：`surge_assessment`、`safety_blocked`。
- 原因码：`SURGE_HIGH_BLOCKED / SURGE_MEDIUM_REVIEW / SURGE_UNKNOWN / SURGE_BOUNDARY_UNKNOWN / SURGE_DATA_MISSING`。
- 语义：`low` 仅为代理判断；`high`/`unknown` 必须由 C 阻断或人工升级，不得自动执行。

## 8. 测试

```
python3 -m pytest tests/safety -q -rs                                  -> 19 passed
python3 -m pytest tests/data tests/models tests/baseline tests/optimization tests/safety -q -rs -> 165 passed, 0 failed, 0 skipped
```

覆盖：正常运行、低流量（high）、高负荷、IGV/BOV 异常、切换过渡、数据缺失、压力越域、版本不匹配、边界缺失、unknown 阻断、JSON 序列化、边界单调性、边界低于 validated_min、包络工件可复现。

## 9. 复现命令

```bash
python3 -m air_station.safety.boundaries      # 生成 surge_boundary.json
python3 -m pytest tests/safety -q -rs
```
