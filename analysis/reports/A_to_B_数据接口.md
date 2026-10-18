# A → B 数据接口（调度算法成员）

面向调度算法（B）。B 只需标准数据与两个运行时接口，**无需读取原始 CSV/Excel/JSON**。

---

## 1. B 直接使用的数据路径

| 文件 | 含义 | 主键 | 时间粒度 |
|------|------|------|----------|
| `data/processed/station_timeseries.parquet` | 站级时序（母管压力/总流量/总功率/负荷/环境） | `time` | 1 分钟 |
| `data/processed/device_timeseries.parquet` | 设备级时序长表（4#/5# 各指标） | `time` + `device_id` | 1 分钟 |
| `data/processed/events.parquet` | 运行事件长表（状态/故障） | `time` + `device_id` | 1 分钟 |
| `data/processed/maintenance.parquet` | 维护保养长表（运行/负载/保养剩余） | `time` + `device_id` | 1 分钟 |
| `artifacts/metrics/forecast_test_predictions.parquet` | 测试集逐点预测（预测+区间+真值） | `time` + `horizon_minutes` | 1 分钟 |
| `data/simulated/rul_sequences.parquet` | RUL 模拟退化序列（origin=simulated） | `episode_id`（+ `time`） | 6 小时 |

> 若在全新环境，先运行 `python -m air_station.data.pipeline --stage standardize --input data`（及 `--stage features`）生成上述前四个文件。

## 2. 字段、单位、缺失规则、来源标记

### 2.1 `station_timeseries.parquet`
| 字段 | 单位 | 说明 | origin |
|------|------|------|--------|
| time | — | 时间键 | real |
| header_pressure_mpa | MPa | 母管压力 | real |
| total_flow_m3_min | m3/min | 总流量(=4#+5#) | inferred |
| total_power_kw | kW | 总功率(=4#+5#) | inferred |
| station_temperature_c | ℃ | 站房温度 | real |
| station_humidity_percent | %RH | 站房湿度 | real |
| load_rate | - | 负荷率 | inferred |
| load_level | - | 0 低谷/1 平段/2 高峰 | inferred |
| environment_interpolated | bool | 环境掉点/插值标记 | inferred |

### 2.2 `device_timeseries.parquet`（长表，每行一个设备一个时刻）
| 字段 | 单位 | 说明 | origin |
|------|------|------|--------|
| time / device_id | — / — | 键（device_id ∈ {4,5}） | — |
| device_type | — | centrifugal | real |
| running | - | 运行状态（事件优先+功率兜底） | inferred |
| state_source | — | event_record / power_fallback | inferred |
| discharge_pressure_mpa | MPa | 排气压力 | real |
| flow_m3_min | m3/min | 实时流量 | real |
| power_kw | kW | 有功功率（float64） | real |
| load_ratio | - | 加载率（float64） | inferred |
| specific_power | kW/(m3/min) | 比功率（float64） | inferred |
| igv_percent / bov_closed_percent | % | 导叶开度/放空阀闭度 | real |
| current_proxy_a | A | 代理电流(=(A相+C相)/2) | inferred |
| vibration_stage_1/2/3_mm_s | mm/s | 三级振动 | real |
| discharge_temperature_c | ℃ | 排气温度 | real |
| runtime_hours / load_hours | h | 累计运行/负载 | real |
| maintenance_remaining_hours | h | 距下次保养 | real |
| fault_code | - | 故障码(26=弱标签) | real |
| quality_valid / quality_reason | bool / cat | 质量标记 | inferred |
| temperature_available | bool | 前6天长缺失期为 False | inferred |
| maintenance_sentinel_removed | bool | 哨兵已剔除 | inferred |
| fault_label_quality | cat | weak / none | inferred |
| possible_station_level_event | bool | 疑似站级共因 | inferred |

缺失规则：不插补（保留 NaN）；`current_proxy_a` 在极小流量下为 NaN；哨兵值(65535/4.29e9)已置 NaN。
**origin 标记**：`real` 直接来自主办方；`inferred` 由真实字段计算/推断；`simulated` 预留(喘振边界等)。

### 2.3 `events.parquet` / `maintenance.parquet`
- events：`time, device_id, status_code, running, fault_code, warning_code, fault_label_quality, possible_station_level_event`
- maintenance：`time, device_id, runtime_hours, load_hours, maintenance_remaining_hours, maintenance_sentinel_removed`

### 2.4 `forecast_test_predictions.parquet`
`time, horizon_minutes, predicted_flow, lower_bound, upper_bound, actual_flow`（单位 m3/min；测试集 2026-08-21 ~ 09-12）。

### 2.5 `rul_sequences.parquet`（模拟，origin=simulated）
| 字段 | 单位 | 说明 |
|------|------|------|
| time | — | 时间（每个 episode 连续，6 小时步长） |
| device_id | — | 4 / 5 |
| episode_id | — | 退化周期唯一标识，如 `4-018`；**保养复位后产生新 episode** |
| scenario | — | normal / slow / fast / noisy |
| rul_hours | h | **真实模拟 RUL 标签**（非真实寿命） |
| vib1_current / vib2_current / vib3_current | mm/s | 振动（随退化指数上升） |
| discharge_temp_c | ℃ | 排气温度 |
| oil_tank_c | ℃ | 油箱油温 |
| current_proxy_a | A | 代理电流 |
| unit_flow_power | kW/(m3/min) | 单位流量功率 |
| maintenance_reset | bool | 该点为保养复位点 |
| origin | — | 固定 `simulated` |

## 3. B 获取在线预测

```python
from air_station.models.forecasting.service import forecast_json
fc = forecast_json()                     # 默认 15/30/60 分钟
# fc["predictions"] -> [{horizon_minutes, target_time, predicted_flow, lower_bound, upper_bound}, ...]
```

## 4. B 获取当前健康约束

```python
from air_station.models.health.service import health_json
h = health_json()
# h["devices"] -> [{device_id, health_score, risk_level, anomaly_score, ...}, ...]
```

## 4.1 B 获取 RUL 剩余寿命（模拟）

```python
from air_station.models.rul.service import rul_json
r = rul_json()
# r["devices"] -> [{device_id, estimated_rul_hours, lower_bound_hours, upper_bound_hours, method}, ...]
```

- `validation_mode = "simulation"`，为**模拟退化模型**的输出，用于寿命约束/演示；
- 作为调度约束时可参考：`estimated_rul_hours` 越低，可对机组执行更保守的加载策略；
  是否停机仍由 B 的安全规则与人工确认决定；
- **不得**将 `estimated_rul_hours` 表述为真实寿命或维修依据。

## 5. 风险等级如何供 B 使用

| risk_level | 建议的调度含义 |
|------------|----------------|
| 正常 | 不额外限制 |
| 关注 | 可增加轻度惩罚（降优先级/减少加载） |
| 预警 | 建议降载或降低调度优先级 |
| 严重 | 是否停机由 **B 的安全规则与人工确认** 决定；A **不直接下发控制指令** |

> 健康结果 `risk_level` 是**无监督风险提示**，不是真实故障定论；`fault_code` 26 为两机同步的
> 弱标签，疑似站级共因事件，不可当独立故障真值。
