# B 侧历史回放数据包 (A6)

## 文件
- `forecast_dev_dispatch.parquet` / `forecast_validation_dispatch.parquet`
  六列：as_of, target_time, horizon_minutes, predicted_flow, lower_bound, upper_bound
  主键：(as_of, horizon_minutes)；时域 15/30/60；单位 m3/min；target_time = as_of + horizon（瞬时点）
- `forecast_scoring_actuals.parquet`
  列：split, as_of, target_time, horizon_minutes, actual_flow, actual_available
  join 不到真实流量时 actual_flow=NaN 且 actual_available=False（不 drop）
- `replay_manifest.json` 清单与哈希

## 使用
- 调度时刻 T 读取 as_of=T 的三条记录；禁止把 actual_flow 作输入。
- 事后评分按 (as_of, horizon_minutes) 关联 scoring 表，用 actual_available 过滤。
- 验证段为 rolling_update_evaluation，非静态 holdout。

## 时区/频率
timezone=Asia/Shanghai；timestamp_storage=timezone-naive local wall time；
源 1 分钟；发出 5 分钟。
