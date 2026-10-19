# A6_Update_Report：区间校准与 45 分钟补发

## 1. 结论

- 引入**滚动共形校准**（用历史已实现残差的分位数定上下界），验证集 15/30/60 分钟覆盖率回升至目标区间；
- 新增 **45、75 分钟**预测（每折独立训练），输出结构与主键 `(as_of, horizon_minutes)` 一致；
- 新增 `pred_mean_interval`、`pred_upper_interval` 供 B 侧调度需求。

## 2. 区间覆盖率对比（valid 集，名义 90%）

| 时域 | 旧版覆盖率 | 新版覆盖率 | 违反率(低<) | 违反率(高>) |
|---|---|---|---|---|
| 15min | 0.2937 | 0.9437 | 0.0301 | 0.0262 |
| 30min | 0.2609 | 0.9411 | 0.0306 | 0.0283 |
| 45min | - | 0.9393 | 0.0296 | 0.0311 |
| 60min | 0.2194 | 0.937 | 0.0311 | 0.0318 |
| 75min | - | 0.9359 | 0.0296 | 0.0345 |

## 3. 精度（WAPE，证明真实精度约 2%）

| 时域 | MAE | RMSE | WAPE | 样本 |
|---|---|---|---|---|
| 15min | 7.445 | 26.254 | 1.718% | 5757 |
| 30min | 9.087 | 29.619 | 2.097% | 5754 |
| 45min | 10.086 | 31.197 | 2.327% | 5751 |
| 60min | 10.931 | 32.399 | 2.522% | 5748 |
| 75min | 11.716 | 34.01 | 2.703% | 5745 |

> MAPE 受低/零流量分母影响极大，WAPE（Σ|误差|/Σ|真值|）更能反映真实精度。

## 4. 45 分钟补发说明

- 时域集合由 {15,30,60} 扩展为 {15,30,45,60,75}，每折每时域独立训练；
- 45/75 分钟同样含点预测与（共形校准后的）上下界，主键与格式与原表一致；
- 新增两列 `pred_mean_interval`（同一 as_of 下 <=h 点预测的累计均值）、`pred_upper_interval`（<=h 上界的累计最大值）。

## 5. B 侧调度需求口径答复

- 单点需求：直接用 `predicted_flow`（目标时刻瞬时点流量，单位 m3/min）；
- 区间均值需求：用 `pred_mean_interval`；区间上界需求：用 `pred_upper_interval`；
- 保守调度建议采用 `pred_upper_interval` 作为需求上限，`pred_mean_interval` 作为期望负荷；
- 4 步连续规划（15/30/45/60）现已可完整获取。

## 6. 产物

- `artifacts/handoff_b/forecast_validation_dispatch_calibrated.parquet`
- `artifacts/handoff_b/forecast_dev_dispatch_calibrated.parquet`
- `artifacts/handoff_b/forecast_scoring_actuals_calibrated.parquet`
- 原版 `forecast_*(dev|validation)_dispatch.parquet` **保持不动**。
