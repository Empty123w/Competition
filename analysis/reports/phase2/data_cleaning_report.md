# 阶段 2 数据清洗报告

由 `python -m air_station.data.pipeline --stage standardize` 生成。

## 1. 标准输出表

| 文件 | 行数 | 列数 |
|------|------|------|
| station_timeseries.parquet | 264,961 | 9 |
| device_timeseries.parquet | 529,922 | 27 |
| events.parquet | 529,922 | 8 |
| maintenance.parquet | 529,922 | 6 |

## 2. 验收自检

| 检查项 | 结果 |
|--------|------|
| raw_hash_unchanged | PASS |
| no_sentinel_in_cleaned | PASS |
| no_constant_in_features | PASS |
| long_gap_not_interpolated | PASS |
| station_flow_sum | PASS |
| station_power_sum | PASS |
| device_rows | PASS |
| quality_flags_present | PASS |

**总计: PASS**

## 3. 质量标记列

- 站级: `environment_interpolated` (温湿度掉点/插值)
- 设备级: `temperature_available`, `maintenance_sentinel_removed`, `fault_label_quality`, `possible_station_level_event`, `quality_valid`, `quality_reason`

## 4. 清洗规则要点

- B相电流恒零: 保留原始, 不入特征; 生成 `current_proxy_a`=(A相+C相)/2
- 大气压恒常量: 保留记录, 不入模型
- 中冷/后冷前6天长缺失: 保留为空, 不插值, 标记 `temperature_available=false`
- 环境温湿度=0 掉点: 仅短缺口插值并标记 `environment_interpolated`
- 维护计数器 65535/4294970000: 精准置 NaN, 标记 `maintenance_sentinel_removed`
- 状态: 8/10 用事件记录, 其余异常值用功率>1kW 兜底, 标记 `state_source`
- 故障码26: 保留, 标记 `fault_label_quality=weak`, `possible_station_level_event=true`
