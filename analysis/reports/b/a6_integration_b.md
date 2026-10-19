# A6 正式交付接入与预测质量验收（B 侧）

- 负责人：成员 B ｜ 集成分支：`feat/b-int-a6`（本地，**未推送**）
- B 基准分支：`feat/b-optimization-aligned` @ `6e3b6e6`（**保持不动**）
- A6 上游：`Wxyun113/Competition` main `519dde37d906a865afc2a0b165ff8b39ca844409`
- 纪律：不改冻结 B1–B4；不调参；不追求 6% 达标；不 push/合并/PR；保留历史结果

---

## 1. 安全同步 A6

| 项 | 值 |
|---|---|
| 共同祖先 | `e922de0`（A6 与 B 分支分叉，互不包含） |
| A6 提交 | `513fe4b`(滚动回放+B 交接包)、`519dde3`(历史健康+RUL 导出) |
| 集成分支 | `feat/b-int-a6`（由 B 基准分支创建） |
| P0 记录 | 先在集成分支提交上一轮 P0 修正（`9be5f90`，本地待验收），使工作树干净后再引入 A6 |
| 合并 | `merge --no-ff 519dde3`（`ca5cf0d`）；**唯一冲突 `.gitignore`**，按两侧规则并集解决 |
| 结果 | B0–B5 代码/实验**全部保留**（已校验关键文件存在）；**未强制覆盖/重置/丢弃** |

- A6 文件均为 A 侧新增（`artifacts/handoff_b/*`、`artifacts/metrics/{forecast_rolling,health_history,rul_history}*`、`src/air_station/models/{forecasting,health,rul}/*`、`configs/rolling_forecast.yaml`、相关测试与报告）；除 `.gitignore` 外与 B 无重叠。

## 2. A6 数据验收（B 侧独立核查）

验收脚本：`analysis/scripts/a6_data_acceptance.py` → `analysis/reports/b/a6_data_acceptance.json`

| 文件 | 行数 | 行数匹配 | SHA256 匹配 | 列匹配 |
|---|---|---|---|---|
| forecast_dev_dispatch.parquet | 110,571 | ✓ | ✓ | ✓ |
| forecast_validation_dispatch.parquet | 17,259 | ✓ | ✓ | ✓ |
| forecast_scoring_actuals.parquet | 127,830 | ✓ | ✓ | ✓ |

结构（三表一致）：主键 `(as_of,horizon_minutes)` **唯一**；`target_time == as_of + horizon` **全部成立**；时域 **15/30/60**；发出频率 **5 min**；时区 **Asia/Shanghai（tz-naive 本地墙钟）**；单位 **m³/min**；dev 覆盖 03-26~07-31、valid 08-01~08-20。

### 预测质量（B 独立复算；valid）

| 时域 | n | MAE | MAPE(A口径) | MAPE_mf50 | WAPE | 90%覆盖 | 低于下界 | 高于上界 | 低估比例 | 零流量样本 |
|---|---|---|---|---|---|---|---|---|---|---|
| 15min | 5,757 | 7.445 | **34.097** | 2.048 | 1.718 | **0.2937** | 0.3722 | 0.3340 | 0.4798 | 4 |
| 30min | 5,754 | 9.087 | **34.560** | 2.556 | 2.097 | **0.2609** | 0.3752 | 0.3639 | 0.4955 | 4 |
| 60min | 5,748 | 10.931 | **35.025** | 3.122 | 2.522 | **0.2194** | 0.3923 | 0.3883 | 0.4955 | 4 |

- **A 报告值精确复现**：A 的 MAPE 分母取 `max(actual, 1.0)`（config `mape_floor_kw=1.0`）；用同口径 B 得 34.097/34.560/35.025，**与 A 完全一致**。
- **MAPE 34–35% 的成因**：valid 段有 **4 个 actual=0** 样本；在分母 floor=1.0 下单个近零样本贡献 ~|err|/1 ≈ 数百 %÷n，主导整体 MAPE。**剔除零流量后 valid MAPE≈2.0–3.1%**，**WAPE 仅 1.7–2.5%**（绝对误差相对总量很小）。
- **区间覆盖率 21.9–29.4%（名义 90%）**：**欠覆盖**；下界违反 37–39%、上界违反 33–39%（近似对称）→ **区间整体过窄**（滚动残差分位校准偏紧），非单侧偏移。baseline lag-1 覆盖≈0（区间近乎退化）。
- dev 段零流量样本 **932**，A 口径 MAPE 36.1/50.7/70.4% 同因。
- 缺失率：`actual_available` 全为 True（**0% 缺失**）。
- **未重训/未改 A 模型**；发现的问题形成问题单反馈 A（见 §6）。

## 3. 预测接口适配

- `forecast_adapter.py` 兼容 **A6 `as_of`** 与旧版 `time`；校验 **`target_time == as_of + horizon`**（不符抛错）。
- 评分真值经 `attach_scoring_actuals()` 从 **独立评分表**载入，**仅用于事后评分**，**不进入调度输入**（测试覆盖）。
- 时间对应（15min 步长、4 步规划器 ↔ A6 15/30/60 瞬时点）：
  - 步 k 目标时刻 = `as_of + (k+1)*15`；需要 horizon 15/30/45/60；
  - **45min 缺失**：`missing_policy="hold_previous"` 取 **≤目标 horizon 的最大可用值（30min）**，**不使用未来信息或 actual_flow**；`strict` 模式报错。
  - **瞬时点 ≠ 区间平均**：A6 为 `point_in_time`，**不得**当区间平均流量积分；本适配按“该步需求目标”使用（工程近似，待 A/B 约定）。
- dev 覆盖自 **03-26** 起：`as_of < 03-26` 无预测 → **不补齐**，走保守保持（测试）。

## 4. 历史健康与 RUL 接入

新增 `src/air_station/optimization/health_rul_adapter.py`（逐机 **as-of 向后关联**，无未来泄漏）：

| 数据 | 频率 | 可用性（A6 实测） | 接入规则 |
|---|---|---|---|
| 健康 `health_history` | 每小时 | quality_valid 7053/8164；invalid 1111（`not_running` 852、`baseline_insufficient` 259） | 需 `quality_valid` 且未过期且 staleness≤`max_staleness_minutes(60)` |
| RUL `rul_history` | 每 6 小时 | **OOD 998/1252（≈80%）**；quality_valid 仅 252 | 需 **quality_valid ∧ ¬OOD ∧ fresh ∧ 未过期 ∧ 来源满足**；`model_training_origin=simulated` 标 `evidence=simulation` |

- **回退链**：RUL 无效 → 回退**有效健康等级**；健康亦无效 → **unknown 保守**。
- **RUL 仅可选软惩罚**：不触发停机、不降低备用、不放松安全（测试 `test_rul_never_relaxes_safety_constraints` 验证 `reserve=extra 5%`、执行权限不变）。

## 5. 最小集成验证

测试 `tests/optimization/test_a6_integration.py`（固定场景：normal/high/low/预测缺失/健康缺失/RUL OOD）：

- 预测**时间/字段/单位**：`target=as_of+horizon`、`lower≤pred≤upper`、范围合理；actual 不入输入。
- 调度**接受 A6 输入**：normal/high/low 三场景 `optimize_dispatch` 返回结构化结果，`requires_field_review=True`、`execution_permission=simulation_candidate_only`。
- **预测缺失** → `INSUFFICIENT_DATA` + 保守 `fallback_candidate`。
- **健康缺失/RUL OOD** → 正确回退（unknown / health）。
- 安全约束未放松。

## 6. 需 A 确认的问题（问题单）

1. **区间过窄**：名义 90% 区间在 dev/valid 覆盖仅 36–48% / 22–29%；建议 A 复核滚动残差分位校准（是否含 regime/零流量处理）。
2. **零流量样本**：valid 4 个、dev 932 个 `actual=0`；MAPE 受其主导。建议统一 MAPE 口径（建议同时给出 MAPE_mf50 与 WAPE）。
3. **瞬时点 vs 区间平均**：调度积分需要区间平均/代表流量；A6 仅瞬时点。建议 A 明确“用于调度”的流量口径。
4. **45min 等缺失时域**：A6 仅 15/30/60；4 步规划器需 45/75…。请 A 确认是否补发或认可 `hold_previous`。
5. **dev 起始 03-26**：与开发期 03-12 不一致，03-12~03-25 无预测。

## 7. 文件变更与测试结果

- 变更：`forecast_adapter.py`（A6 兼容+评分分离+步对齐）、新增 `health_rul_adapter.py`、新增 `analysis/scripts/a6_data_acceptance.py`、测试 `tests/optimization/test_{forecast_adapter,health_rul_adapter,a6_integration}.py`。
- 全量回归：`tests/data models baseline optimization safety b5` = **249 passed, 0 failed**（含 A6 带入的 A 侧 `tests/models`）。
- 冻结 `src/air_station/{baseline,safety}`、`configs`、`tests/{baseline,safety}` 未改动。

## 8. 能否进入下一轮正式 B5 连续对照

- **接口层面：可以**。A6 预测可被适配器逐时刻读取（无未来泄漏），actual 仅评估；健康/RUL 可 as-of 关联并保守回退；最小集成通过。
- **质量层面：需先与 A 确认 §6 问题**（尤其区间过窄与流量口径），否则连续对照的“需求口径/区间上界”会受欠覆盖区间影响。
- 说明：08-01~08-20 已反复查看，**仍是重复使用验证窗口，不是新的独立最终测试**.

## 9. 复现

```bash
python3 analysis/scripts/a6_data_acceptance.py
python3 -m pytest tests/optimization/test_forecast_adapter.py tests/optimization/test_health_rul_adapter.py tests/optimization/test_a6_integration.py -q
```
