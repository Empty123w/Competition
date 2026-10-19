# A → B 历史回放数据接口（阶段 A6）

面向调度（B）。预测表优先交付；健康/RUL 不阻塞。

## 1. 两张调度表固定六列

```
as_of, target_time, horizon_minutes, predicted_flow, lower_bound, upper_bound
```
- 不含 `actual_flow`；主键 `(as_of, horizon_minutes)`；时域仅 15/30/60。

## 2. 频率与时间语义

- 源数据 1 分钟；先在 1 分钟时间轴构建因果特征，**再抽取 5 分钟行**用于训练/校准/预测，不插值；
- `as_of` 为预测发出时刻，全部落在原生 5 分钟网格；
- `target_time = as_of + horizon_minutes`；`predicted_flow` 是目标时刻的**瞬时点流量**，单位 m3/min；
- 时区：Asia/Shanghai（本地 wall time，无时区）；发出频率 5 分钟。

## 3. 评分真值表（独立）

`forecast_scoring_actuals.parquet`：`split, as_of, target_time, horizon_minutes, actual_flow, actual_available`
- `actual_flow` 对应 `target_time` 的真实瞬时总流量；
- join 不到真实流量时 `actual_flow=NaN` 且 `actual_available=False`（不 drop，B 用该列过滤）。

## 4. 覆盖范围

- 开发表：2026-03-26 ~ 2026-07-31，共 **110,571 行**；
- 验证表：2026-08-01 ~ 2026-08-20，共 **17,259 行**；
- 2026-03-12 ~ 03-25 为**预热缺失**（`initial_training_and_interval_calibration_warmup`），不补造。
- 开发段每时域最后 `as_of`：15min=23:40、30min=23:25、60min=22:55（保证目标落在分段内）。

## 5. 预热训练/校准样本数（首折，理论值）

15/30/60 分钟训练各 3165/3162/3156 行；校准各 861/858/852 行（实测见审计表）。

## 6. 验证期评估性质

验证段为**预先固定的滚动更新评估**（`evaluation_mode=rolling_update_evaluation`、`rolling_update=true`），
重训日为 07-30 折覆盖 08-01~08-05，其后 08-06、08-13、08-20；不是静态 holdout，不是独立最终测试。

## 7. 每折截止时间与模型哈希

- 每折：训练目标截止 `<` 校准开始 `<` 预测开始；`input_data_cutoff_time <= as_of`；
- 折级元数据（含每折每时域 `model_sha256`/`config_sha256`/`code_commit_sha`）见
  `artifacts/metrics/forecast_fold_metadata.json`；逐行审计见 `forecast_rolling_audit.parquet`
  （审计表不重复存哈希，通过 `fold_id` 映射）。

## 8. 健康历史（无监督滚动）

`artifacts/metrics/health_history.parquet`：小时级，`validation_mode=unsupervised_rolling`；
含 `input_data_cutoff_time`、`baseline_cutoff_time`、`valid_until`（=time+60min）。
B 取数：`health.time <= dispatch.time <= health.valid_until`，且 `quality_valid=true`；
无有效记录用保守回退，禁止向前读未来记录。

## 9. RUL 历史（模拟迁移）

`artifacts/metrics/rul_history.parquet`：6 小时级，`validation_mode=simulation`、
`usage_context=real_telemetry_transfer`；含 `input_data_cutoff_time`、`valid_until`（=time+6h）、
OOD 标记。B 取数需同时满足 `quality_valid=true` 且 `dispatch.time <= valid_until`；
仅作软约束，不触发停机、不放宽安全约束；过期/无效回退健康等级。

## 10. as-of join 示例

```python
# 预测
fc = fc[fc.as_of == T]                       # T 为 5 分钟网格调度时刻, 取到 15/30/60 三条
# 健康(向后 as-of)
h = health[(health.device_id == d) & (health.time <= T) & (T <= health.valid_until) & health.quality_valid]
h = h.sort_values("time").iloc[-1]
# RUL(向后 as-of)
r = rul[(rul.device_id == d) & (rul.time <= T) & (T <= rul.valid_until) &
        rul.quality_valid & (rul.validation_mode == "simulation") &
        (rul.usage_context == "real_telemetry_transfer") & (rul.stale_hours <= rul.max_staleness_hours)]
```
超期或缺失 → 回退（健康按未知、RUL 用健康等级），禁止使用未来记录。

## 11. 冻结测试表

`forecast_test_predictions.parquet`/`.csv` 仍为**唯一最终测试预测表**，哈希未变；
本次历史回放产物不替代它，也不作为独立最终测试成绩。
