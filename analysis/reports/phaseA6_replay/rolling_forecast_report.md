# 阶段 A6 滚动回放预测报告

- 折数：22（每 7 天重训，共 66 次模型训练）
- 样本频率：源 1 分钟，预测发出 5 分钟
- 时域：[15, 30, 60] 分钟；目标：point_in_time（m3/min）
- 验证段标记：rolling_update_evaluation

## 1. 指标（模型 vs lag_1 基线）

| 分段/时域 | 模型 MAE | 模型 RMSE | 模型 MAPE | 区间覆盖 | 基线 MAPE |
|-----------|----------|-----------|-----------|----------|-----------|
| dev_15min | 7.219 | 26.109 | 36.081 | 0.4811 | 31.126 |
| dev_30min | 8.861 | 30.827 | 50.719 | 0.4364 | 45.73 |
| dev_60min | 11.053 | 36.459 | 70.42 | 0.3561 | 63.937 |
| valid_15min | 7.445 | 26.254 | 34.097 | 0.2937 | 34.054 |
| valid_30min | 9.087 | 29.619 | 34.56 | 0.2609 | 34.522 |
| valid_60min | 10.931 | 32.399 | 35.025 | 0.2194 | 35.019 |

> 说明：本表为滚动回放算法开发/评估材料，非独立最终测试；真实历史回放不使用冻结全训练段模型；模型与 lag_1 指标均如实展示。

## 2. 防泄漏要点

- 每折训练目标 < 校准开始；校准目标 < 预测开始；输入截止 <= as_of；
- 1 分钟时间轴构建因果特征后再抽 5 分钟行（lag_1 仍为 1 分钟前）；
- 环境温湿度仅因果前向填充；缺失率>阈值整折回退 core_no_environment。

## 3. 输出

- `artifacts/handoff_b/forecast_dev_dispatch.parquet`（六列）
- `artifacts/handoff_b/forecast_validation_dispatch.parquet`（六列）
- `artifacts/handoff_b/forecast_scoring_actuals.parquet`（含 actual_available）
- `artifacts/metrics/forecast_rolling_audit.parquet` + `forecast_fold_metadata.json`

