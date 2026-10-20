# A6 test 数据可用性与独立性专项核查（只读；未训练/未长回测/未改代码/未推送）

核查范围：`feat/b-int-a6` 工作树、`origin/main`（`e922de0`）、本地 `data/`、`artifacts/metrics/`、`artifacts/handoff_b/`、`configs/`、`src/air_station/models/forecasting/`、`analysis/scripts/`。

---

## 一、已核实事实

### 1. test 原始 SCADA 数据：存在且完整
| 文件（本地工作树，git 未跟踪） | 时间范围 | 记录数 | 含 test(08-21~09-12)? |
|---|---|---|---|
| `data/设备运行参数.csv` | 2026-03-12 00:00 ~ 09-12 00:00 | 264961（1min） | 是（流量/功率/排气压力/IGV/BOV/电流） |
| `data/管网运行数据.xlsx` | 同上 | 264961 | 是（母管压力/支路流量/压差） |
| `data/用气负荷数据.csv` | 同上 | 264961 | 是（总流量=4#+5#） |
| `data/设备状态监测.csv` / `运行事件记录.xlsx` / `环境参数.xlsx` | 同上 | 264961 | 是 |
| `data/processed/station_timeseries.parquet` | 03-12 ~ 09-12 | 264961 | 是（母管压力/总流量/总功率…） |
| `data/processed/{device_timeseries,events,maintenance,health_features}.parquet` | 03-12 ~ 09-12 | 529922 | 是 |

→ **test 段原始历史记录（运行/流量/功率/母管压力）完整存在**。

### 2. A6 滚动回放 test 预测：不存在
- `artifacts/handoff_b/` 仅有 `forecast_dev_dispatch*`、`forecast_validation_dispatch*`、`forecast_scoring_actuals*`（dev 03-26~07-31、valid 08-01~08-20），**无 test**。
- `artifacts/metrics/forecast_rolling_audit.parquet`：`as_of 2026-03-26~2026-08-20`（滚动折止于 valid）。
- `origin/main` 与其余远程分支（`feat/b-optimization`、`feat/b-optimization-aligned`）均**无 `handoff_b`**、无滚动 test 预测。

### 3. 但存在 A 静态模型的 test 预测文件
- `artifacts/metrics/forecast_test_predictions.parquet`（及 `.csv`），`origin/main` 等分支均含。
- 实测：列 `time, horizon_minutes, predicted_flow, lower_bound, upper_bound, actual_flow`；**时间 2026-08-21 00:00 ~ 09-11 23:45**；`n_issues=31666`（**1min** 网格）；**时域仅 15/30/60**（无 45/75）；`actual_flow` 全非空；**94938 行 = 31666×3**。
- 来源：`configs/forecasting.yaml` `split.test 08-21~09-12`（L32）的**静态单模型**管线（train 03-12~07-31、valid 08-01~08-20）；由 `src/air_station/baseline/pipeline.py` **L27** `DEFAULT_FORECAST_PATH = "artifacts/metrics/forecast_test_predictions.parquet"` 使用。

### 4. A6 能否生成 test 预测：代码支持，当前未生成
- **未生成**：`rolling_backtest.py` `build_folds` **L35-44**：`valid_end = cfg["valid"]["end"]`（=08-20），`while s <= valid_end`，`pred_end = min(s+step, valid_end+1min)` → 折只到 08-20。
- **支持生成**：把折区间扩到 09-12 即可；输入齐备——`data/processed/forecast_features.parquet`（03-12~09-12，264961 行）存在，模型目录可写；`configs/rolling_forecast.yaml` 时域 `[15,30,45,60,75]`，输出结构与 dev/valid 一致。
- **需改动**：`rolling_forecast.yaml` 的 `valid.end`（或新增 test 区间）+ `export_history.py` L76-101 / `calibrate.py` L74-83 的 split 标注区分 test。
- **不需 A 提供未提交模型/数据**：参数固定（见下），无需在 test 上选参。

### 5. 独立性核查
- **未用于 A 超参/模型选择**：`configs/forecasting.yaml` 参数固定（无搜索）；`train.py` **L90** 仅用 `train` 拟合；`valid` 仅用于计算区间残差（**L93** `signed_residual_quantiles`）；`test` 仅在 `evaluate.py` **L72-88** 报告指标。滚动侧 `rolling_forecast.yaml` audit：`hyperparameter_source: frozen_config_no_fold_tuning`、`model_selection_data_cutoff: not_applicable_no_fold_selection`。
- **A6 信息边界/校准因果**：前一轮 `A6_SPLIT_VERIFICATION.md` 已核实——折叠 train 目标 `< cal_start`、cal `target_time<S`、`cutoff=S`；校准 `merge_asof(direction="backward", allow_exact_matches=False)` 仅用已实现残差。**无未来泄漏**。
- **⚠ test 已参与 B 组早期开发/回测（关键）**：
  - `analysis/scripts/b5_gap_analysis.py` **L3**：「多日期回测 (**冻结测试窗口 2026-08-21~09-11**)」；
  - `analysis/scripts/multistep_optimization.py` **L3**：「不在 **B5 窗口 (2026-08-21~09-11)** 调参」；
  - `analysis/reports/b/b5_backtest_metrics.json` `meta.window = ["2026-08-21 00:00:00","2026-09-11 23:00:00"]`、`n_samples=6325`（早期 B5 正式回测即在此窗口）；
  - 数据来源为静态 `forecast_test_predictions.parquet`（`pipeline.py` L27）。
- 附：本会话部分 KPI 脚本 `analysis/scripts/kpi_pressure_validation.py` **L20** 与 `kpi_surge_events.py` **L21** 把 08-21~09-11 标为 `"valid"`，实为 B5 test 窗口（命名待统一，不影响本核查结论）。

---

## 二、合理推断

1. `forecast_test_predictions.parquet` 属 **A 静态管线（阶段 A0/A4）** 的 test 集预测，**早于 A6 滚动回放**；其区间为 valid 残差分位（`train.py` L93），**非 A6 共形校准**，也无 45/75。
2. test 段对 **A** 是"时间顺序测试段"（未用于 A 训练/选参），但对 **B** 是"**已用于早期 B5 开发与调参的窗口**" → 不满足"未参与 B 开发的独立段"。
3. 因此把 08-21~09-12 作为 B 的"额外离线评估段"在**统计上可行**（数据在、时序在后），但**不构成盲测**，须在报告中声明"已被 B 早期使用"。

---

## 三、无法确认

- A 是否**计划/已另行**为 test 生成滚动预测（当前仓库内无）——需 A 确认。
- 官方是否认可 08-21~09-12 作为评分窗口（赛题未规定，见 `SAITI_KPI_VERIFICATION.md`）。
- 45/75 分钟是否纳入 B 规划时域口径。

---

## 四、最终结论（A–E）

**A. test 数据是否存在？** **存在**。原始 SCADA 完整覆盖 03-12~09-12（含 test 08-21~09-12），设备运行/流量/功率/母管压力均在。
证据：`data/设备运行参数.csv`、`data/管网运行数据.xlsx`、`data/用气负荷数据.csv`、`data/processed/station_timeseries.parquet`（264961 行，03-12~09-12）。

**B. A6 test 预测是否存在？** **A6 滚动 test 预测：不存在**（`artifacts/handoff_b/` 仅 dev/valid；`forecast_rolling_audit.parquet` 至 08-20）。
**但 A 静态模型 test 预测存在**：`artifacts/metrics/forecast_test_predictions.parquet`（08-21~09-11，15/30/60，含 actual）。
证据：`rolling_backtest.py` L35-44、`configs/rolling_forecast.yaml` L26-29、`pipeline.py` L27。

**C. test 是否适合成为 B 的额外离线评估段？** **可作为附加评估段，但不是盲测**。原因：A 未用于训练/选参（`train.py` L90/L93、`evaluate.py` L72-88），但 **B 早期 B5 已在该窗口回测/调参**（`b5_gap_analysis.py` L3、`b5_backtest_metrics.json` meta.window）。须声明"已使用"。

**D. 能否直接开展 B5 test 回测？** **不能直接**（用冻结 B1/B3+B4/bal/LRB-t10 且口径一致）：
- A6 滚动 test 预测不存在；
- 唯一可用的 `forecast_test_predictions.parquet` 与 B harness 不匹配：字段用 `time`（非 `as_of`）、**时域仅 15/30/60 缺 45**（`multikpi_harness.py` **L89** 网格要求 15/30/45/60 → 会得 **0 步**）、1min 网格、无共形校准/45/75；
- 初始状态可用（station/device parquet 覆盖 test）。
- 若要用：需 (a) 由 **A 生成滚动 test 预测**（代码支持：扩 `valid.end` 到 09-12）；或 (b) B 侧适配静态预测（补 45=`hold_previous`、接受未校准区间），但口径与 dev/valid 不一致，属工程折中。

**E. 谁来解决**：
- **COSMO（B）可自行解决**：扩展滚动折区间生成 A6 一致 test 预测（改 `rolling_forecast.yaml` + export/calibrate split 标注）；harness 增加对缺 45 的 `hold_previous` 适配；统一 test/valid 命名。
- **必须向 A 确认**：是否补发滚动 test 预测（`as_of` 口径、15/30/45/60/75）；`forecast_test_predictions.parquet` 与 A6 校准区间口径差异的取舍；45/75 是否纳入。

---

本轮仅核查；未训练 A6、未运行长回测、未改代码/数据、未推送、未改 main。
