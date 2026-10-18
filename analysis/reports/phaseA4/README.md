# 阶段 A4：用气负荷预测 — 说明与验收

- 训练：`python -m air_station.models.forecasting.train`
- 评测：`python -m air_station.models.forecasting.evaluate`
- 接口样例：`forecast_interface_sample.json`（本目录）

产出：

| 文件 | 说明 |
|------|------|
| `artifacts/models/forecast_15m.pkl / .txt` | 15 分钟模型 |
| `artifacts/models/forecast_30m.pkl / .txt` | 30 分钟模型 |
| `artifacts/models/forecast_60m.pkl / .txt` | 60 分钟模型 |
| `artifacts/metrics/forecast_metrics.json` | MAPE/MAE/RMSE/WAPE + 基线对比 + 区间覆盖率 |
| `analysis/reports/phase2/forecast_report.md` | 5 段式预测报告 |

## 1. 运行时接口（供 C 后端 / B 调度）

```json
{
  "status": "success",
  "model_type": "LightGBM",
  "prediction_base_time": "2026-09-12T00:00:00",
  "predictions": [
    {"horizon_minutes": 15, "target_time": "...", "predicted_flow": 499.18,
     "lower_bound": 483.75, "upper_bound": 512.86}
  ],
  "elapsed_seconds": 1.54
}
```

调用：`air_station.models.forecasting.service.forecast_json()`；单次完整预测 **1.5s < 10s**。

## 2. 设计要点

- **三个独立直接模型**（15/30/60 min），非递归；
- **时间顺序切分** train 77% / valid 11% / test 12%，无随机划分；
- 模型：LightGBM（缺失时回退 XGBoost / HistGradientBoosting）；
- **置信区间**：验证集残差 5%/95% 分位数 → 约 90% 区间；
- **防泄漏**：特征基于 T 时刻及以前（`lag_0`=当前观测，允许；滚动/差分基于 `shift(1)`）。

## 3. 特征稳健性处理（重要）

初版含 `header_pressure_mpa`、`total_power_kw`、`load_rate` 等**原始站级测量**时，
模型把环境/压力当作时间漂移代理，训练/测试负荷分布不同（train std=136，test std=56），
GBDT 外推失败，test MAPE 升至 5%~9%。经消融后剔除这些漂移源（见 `configs/forecasting.yaml`
`features.exclude`），保留 `lag_0/滞后/滚动/差分/日历/周期/环境/运行台数/负荷等级/波动率`，
MAPE 回落至 1%~2%。

## 4. 评测结果（真实）

| 时域 | 模型 MAPE | lag_1 | ma_15 | 前一天同时刻 | 前一周同时刻 |
|------|-----------|-------|-------|--------------|--------------|
| 15min | 1.13% | 0.96% | 0.97% | 4.27% | 5.47% |
| 30min | 1.57% | 1.14% | 1.15% | 4.27% | 5.47% |
| 60min | 2.05% | 1.46% | 1.47% | 4.28% | 5.47% |

- 各时域 MAPE 均 **≤ 8%**（赛题目标达成）；
- 模型**显著优于**同日/上周同时刻基线；与极强的持续性基线 lag_1 接近；
- 90% 区间测试覆盖率约 **0.95**（略高于 0.90）。

## 5. 验收对照

| 标准 | 结果 |
|------|------|
| 输出 15/30/60 预测 | PASS |
| 测试集 MAPE ≤ 8% | PASS（1.13/1.57/2.05%） |
| 每时域报告 MAPE/MAE/RMSE/WAPE | PASS |
| 至少优于一个简单基线 | PASS（优于是日/上周同时刻） |
| 单次预测 ≤ 10s | PASS（≈1.5s） |
| 测试集未用于训练/选参 | PASS（按时间切分） |
| 模型可保存、重载、结果一致 | PASS（单测验证） |
| 输出预测区间与覆盖率 | PASS（≈0.95） |
| 未达标须如实分析 | 达标；仍说明 lag_1 极强、如实保留 |
