# 工作状态恢复审计（只读）

- 日期：2026-10-19 ｜ 执行方式：只读检查（未 reset/clean/checkout/merge/cherry-pick/推送）
- 路径：`/workspace`

## 1. 当前分支 / HEAD

| 项 | 值 |
|---|---|
| 当前分支 | `feat/b-int-a6` |
| HEAD | `46e6a4e feat(b): integrate A6 forecast/health/RUL handoff (adapter, acceptance, tests)` |
| B 基准分支 | `feat/b-optimization-aligned` @ `6e3b6e6`（未动） |
| origin 上 B 基准 | `6e3b6e6`（与本地一致，未推送新内容） |

## 2. 关键提交是否存在

| 提交 | 状态 | 说明 |
|---|---|---|
| `6e3b6e6` | 存在 | B5 归档（feat/b-optimization-aligned） |
| `9be5f90` | 存在 | **B5 P0 修复**（feat/b-int-a6：单位/CRN/因果/窗口/unknown/约束验证器/加载率 SEC/预测对齐） |
| `ca5cf0d` | 存在 | A6 合并（`merge --no-ff 519dde3`，仅 `.gitignore` 冲突已解） |
| `46e6a4e` | 存在 | A6 集成（预测适配/健康-RUL as-of/验收/最小集成测试） |
| `519dde3` | 存在 | A6 上游（旧 handoff_b） |
| `0330cf4` | **存在（仅 FETCH_HEAD，未集成）** | A：区间欠覆盖校准 + **45/75 分钟补发** + 调度需求口径 |
| `1f36006` | **存在（仅 FETCH_HEAD，未集成）** | A：文档修正名义覆盖率口径 **1-α=88%** + 需求列语义 |
| reflog | 完整 | `9be5f90 → ca5cf0d → 46e6a4e` 均在 reflog，无丢失 |

## 3. 工作区状态（未提交）

| 文件 | 状态 |
|---|---|
| `src/air_station/optimization/forecast_adapter.py` | 已修改（**仅字段重命名 `byissue_ts → by_issue`**，功能等价，6+/6-） |
| `analysis/scripts/a6_b5_continuous.py` | 未跟踪（A6 连续回测脚本，已修 combo bug） |
| `analysis/scripts/a6_h45_sensitivity.py` | 未跟踪（45min 敏感性脚本，尚未运行） |
| `analysis/reports/b/a6_b5_continuous.json` | 未跟踪（回测指标） |
| `analysis/reports/b/a6_b5_continuous_ledger.csv` | 未跟踪（逐步账本，约 5.97 MB） |
| `.cosmocode/` | 未跟踪（私有，排除） |

## 4. 报告与产物存在性

| 产物 | 状态 |
|---|---|
| `analysis/reports/b/p0_review_fixes_b.md` | 存在（已跟踪） |
| `analysis/reports/b/a6_integration_b.md` | 存在（已跟踪） |
| `analysis/reports/b/a6_data_acceptance.json` | 存在（已跟踪） |
| `analysis/scripts/constraint_verifier.py`、`health_rul_adapter.py` | 存在（已跟踪） |
| `artifacts/handoff_b/*`（旧 A6） | 存在 |

## 5. 旧回测状态

- **进程：已结束**（`ps` 无 `a6_b5_continuous`/`a6_h45` 进程）。
- 日志：`/tmp/opencode/a6bt.log`（首跑）、`/tmp/opencode/a6bt2.log`（修复后重跑）。
- 结果：`a6_b5_continuous.json` + `_ledger.csv`，**mtime 19:12，runtime 1835 s，0 error 行**；dev 12,284 步 + valid 1,916 步。
- **结论：旧回测结果完整可用**（valid 共同可执行节电 7.50%、大马拉小车 −14.96%；dev 对应 12.12%、−54.34%）。
- 但**基于旧 A6（519dde3）数据**；A 已更新校准区间与 45/75 分钟补发，尚未用于回测。

## 6. 核实结论

**已完成：** B0–B5 归档；B5 P0 修复（9be5f90）；A6 集成（ca5cf0d+46e6a4e，249 passed）；A6 数据验收；旧 A6 驱动的连续回测（含 5 策略、三统计范围、六类结果）；45min 敏感性脚本（未运行）。

**需恢复/继续：**
1. 提交未跟踪的回测脚本/结果与字段重命名（当前在 `feat/b-int-a6` 工作区）。
2. **集成 A 新提交 `0330cf4` + `1f36006`**（新增 `*_calibrated.parquet`、45/75 分钟、需求语义；含 `configs/rolling_forecast.yaml`、`calibrate.py`、`test_calibration.py`）。
3. 用**校准后区间 + 45/75 分钟**重跑连续回测（届时无需 `hold_previous` 回退）。

**数据/代码丢失风险：无**（所有提交可达；工作区文件在磁盘；reflog 完整）。

## 7. 下一步建议（待确认，不自动执行）

- 优先：`git fetch` 已得 `1f36006`；在 `feat/b-int-a6` 上 **merge `0330cf4`（含 `1f36006`）**（预计纯新增，冲突面小）。
- 先提交当前未跟踪成果以固化，再重跑回测（校准区间）并更新报告/总表。
- 未确认前不覆盖现有成果；不推送、不做 B6/B7。
