# A6 dev/valid 划分与评估口径核实（只读，未运行回测、未改代码、未推送）

来源：本地 `feat/b-int-a6` 工作树 + `origin/main`（`e922de0`）。所有结论附文件路径与行号。

---

## 一、已核实事实

### 1. 划分边界与用途（由 A6 预测回放流程定义）
- **`configs/rolling_forecast.yaml` L22-29**：`warmup 2026-03-12~03-25`、`first_as_of 2026-03-26 00:00`、
  **`dev: 2026-03-26 00:00 ~ 2026-07-31 23:59`（L26）**、**`valid: 2026-08-01 00:00 ~ 2026-08-20 23:59`（L27）**、
  `refit_interval_days: 7`、`calibration_days: 3`。
- 划分由 **A 组 A6（滚动回放预测）** 制定，**不是赛题官方、也不是 B 回测脚本**：
  - 生成脚本 `src/air_station/models/forecasting/export_history.py` **L76-82**（`_mask`：`is_dev = as_of<valid_start & target_time<=dev_end`；`is_valid = as_of>=valid_start & target_time<=valid_end`）与 **L96、L99-101**（`split` 赋值、拆分三表）；
  - 校准脚本 `src/air_station/models/forecasting/calibrate.py` **L74-83**（同一 `valid_start/dev_end` 边界，写 `*_calibrated.parquet`）。
- **用途 = A 模型训练/滚动验证划分**，非 B 调度算法的正式评测划分：
  - `artifacts/handoff_b/README.md`：**"验证段为 rolling_update_evaluation，非静态 holdout"**；
  - `artifacts/handoff_b/replay_manifest.json`：`"quality": rolling_update_evaluation 用于验证段`，`folds.count=22`（7 天重训）。

### 2. 时间范围与行数（实测）
| 文件 | as_of 范围 | 5min 发出点数 | 时域 | 行数 |
|---|---|---|---|---|
| `forecast_dev_dispatch_calibrated.parquet` | 2026-03-26 00:00 → 07-31 23:40 | 36861 | 15/30/45/60/75 | 184275 |
| `forecast_validation_dispatch_calibrated.parquet` | 2026-08-01 00:00 → 08-20 23:40 | 5757 | 15/30/45/60/75 | 28755 |

（原始未校准表 `forecast_dev_dispatch.parquet` 仅 15/30/60，见 `replay_manifest.json.outputs`；45/75 由 `calibrate.py` / `A6_Update_Report.md` 补发。）

### 3. B 侧 12284 / 1916 步的来源（B 回测脚本的网格规则）
- B 回测入口 `analysis/scripts/multikpi_harness.py` **L89**：
  `grid = [t for t in sorted(b.by_issue) if t.minute % 15 == 0 and all(h in b.by_issue[t] for h in (15,30,45,60))]`
  → 取 **15 分钟整点发出**且**同时具备 15/30/45/60 四个时域**的时刻。
- 实测：dev 15 分钟整点共 **12287**，其中末尾 3 点（`07-31 23:00/23:15/23:30`）因 `h=60` 的目标时刻超出 `dev_end(07-31 23:59)` 缺列 → **12284**；valid 15 分钟整点 **1919**，末尾 3 点（`08-20 23:00/23:15/23:30`）同理 → **1916**。
- 网格首末：dev `03-26 00:00 → 07-31 22:45`；valid `08-01 00:00 → 08-20 22:45`。**两段之间有空隙 75 分钟**。
- **dev(12284) + valid(1916) = 14200**，即题述"全部 14200 调度步"。

### 4. 时间泄漏检查（结论：未发现未来泄漏）
- **折内训练/校准/预测分离**：`rolling_backtest.py` **L69-77**
  `cal_start=S-3d`；`cal_end=S-1min`；`train = target_time < cal_start`；`cal = target_time<S & TIME∈[cal_start,cal_end]`；`pred = TIME>=S`。
  → 训练目标严格早于校准窗，校准与预测均以 `S` 为界；`cutoff = sub[TIME<=S][TIME].max()`（**L91**）。
- **区间校准因果**：`calibrate.py` **L22-30** `_cal_table` 用 **已实现残差 `|actual-pred|`** 的 `rolling(window_days=30,min_periods).quantile(1-α)`，按 `target_time` 排序；**L57-59** `merge_asof(..., left_on="as_of", right_on="cal_time", direction="backward", allow_exact_matches=False)` → 只取 **target_time < as_of** 的残差分位，`allow_exact_matches=False` 排除同刻。**不引用 as_of 之后的真值**。
- **特征因果**：配置 `rolling_forecast.yaml` features 注释"全部因果; 依赖时间 <= as_of"；`causal_fill_limit_minutes: 30`。
- **真值隔离**：`forecast_adapter.py` **L121-123、L170-189** `attach_scoring_actuals` 仅把 `actual_flow` 写入 `actual_by_target` 供**事后评分**；调度只用 `by_issue` 预测（`forecast_result`/`demand_series`）。多步补齐 `demand_series_for_steps` **L96-118** 明确 `hold_previous`（取 ≤ 目标 horizon 的最大可用值），**不用未来/actual 补齐**。
- **回测用法**：harness 中 `actual_at_target` 仅用于 KPI 事后评估（`bd["obs"]` 列），不作调度输入。

### 5. 赛题原文对划分/基线/口径的规定（`origin/main:赛题详情.docx`，commit `e922de0`）
- 赛题**未规定**训练集/验证集/测试集划分，也**未给出官方评分窗口**；仅给 KPI 目标。
- 6% 口径原文：**"系统综合能效提升 ≥6%（对比人工调度基准）"**，**未给出窗口、基线计算方法与公式**。
- 详见 `analysis/reports/b/SAITI_KPI_VERIFICATION.md`（同批读取）。

### 6. 项目内另有 train/valid/test 配置（**非赛题官方**）
- `configs/forecasting.yaml` **L30-32** 与 `configs/optimization.yaml` **L18-20**：
  `train 03-12~07-31`、`valid 08-01~08-20`、**`test 08-21~09-12`**。
- 该 `test` 窗口**无 A6 预测交付**（A6 dispatch 仅到 08-20）；属**项目自定**划分。

---

## 二、推断（有依据但需确认）

1. **"valid"是 A 的滚动验证段，不是 B 调度算法的独立最终测试段**：README/manifest 均标 `rolling_update_evaluation`；B 已多次查看该段（历史验证证据，非盲测）。
2. **B 的 12284/1916 是"15 分钟决策网格 ∩ A 四时域齐备"的派生量**，非人为设定；末端 3 步缺口由 `target_time ≤ 段末` 的过滤导致。
3. **两段不可简单拼接为一次连续仿真**：段间有 75 分钟空隙，且各段初始状态取自各自首时刻的真实快照（`multikpi_harness.py` `init = PIPE.snapshot_from_frames(...bd["t"].iloc[0])`）。跨段连续跑会把 dev 段末端**仿真态**带入 valid 段，与 valid 首时刻**真实态**不一致。

---

## 三、需要 A 确认的事项

1. **official 评分窗口**：赛题未给；项目配置的 `test 08-21~09-12` 是否才是 B 的正式评测窗口？若需要，**A 需为 08-21~09-12 补发同结构预测包**（当前无）。
2. **`valid` 段的定位**：确认其仅为 A 滚动验证，是否允许 B 作为调度评测段；是否需另留**未查看的独立测试段**。
3. **6% 基线与算法**：官方"对比人工调度基准"的**具体基线轨迹与窗口**、以及综合能效的**计算公式与分母单位**，需主办方/A 明确（现为工程口径 `SEC=ΣE/ΣV`，同窗对比）。
4. **45/75 分钟是否需纳入**：B 多步规划现用 15/30/45/60；A 已发 45/75，是否统一规划时域。
5. **瞬时点 vs 区间平均**：A6 为 `point_in_time`，B 按"该步目标需求"使用（工程近似，`a6_integration_b.md` §3 已标待约定）。

---

## 四、B5 能否在全部 14200 步统一回测的判定

- **时间连续性**：dev 与 valid 在时间轴上相接（`07-31 22:45` → `08-01 00:00`，空隙 75 分钟），**非严格连续**。
- **初始状态**：两段各自以真实快照初始化；一次连续仿真会使 valid 段继承 dev 段**仿真态**，与真实态偏离。
- **B5 最终窗口**：`a6_b5_continuous.py` L4/L12/L331-332 明确使用 **dev+valid**，声明 `uses_B5_final_window=False`、`证据=开发/验证窗口; 验证段重复使用, 非独立最终测试`。
- **正确汇总方式（建议）**：
  1. **分别报告** dev 与 valid 两段（各自独立初始化、各自 state），不合并为单一连续序列；
  2. 若必须汇总为"14200 步"总量，采用**分段初始化 + 分段汇总**（两段指标按步数加权，或按配对可执行子集加权），并在报告中注明**段间 75 分钟空隙**与**初始状态来源**；
  3. 不可跨空隙传递仿真态，也不得用 dev 段仿真态作为 valid 段初值。
- 本项目既有做法符合此建议：`multikpi_dev_0_13000.json`（12284）与 `multikpi_valid_0_2000.json`（1916）**分段独立**产出，未强行拼接。

---

本轮仅核实数据来源与评估口径；未启动回测、未改代码、未推送。
