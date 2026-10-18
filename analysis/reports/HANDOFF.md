# 交接清单（供 B 调度算法 / C 后端对接）

本文件汇总本项目**输入数据、运行时接口、离线产物**的物理位置与用法。
所有路径均相对仓库根目录。

---

## 一、输入数据在哪

### 1. 原始数据（主办方，只读）

```
data/
├── 设备运行参数.csv      压力/流量/电流/功率/IGV/BOV
├── 设备状态监测.csv      振动/温度
├── 用气负荷数据.csv      总流量/负荷率/负荷等级/波动率
├── 管网运行数据.xlsx     母管压力/支路流量/压差
├── 环境参数.xlsx         站房温度/湿度/大气压
├── 运行事件记录.xlsx     状态/故障
├── 维护保养记录.xlsx     运行/负载/保养剩余时间
├── 设备档案.json         2 台机组档案
└── 指标释义.json         字段释义与单位
```

### 2. 标准化数据（A2 产出，模型/服务消费）

```
data/processed/
├── station_timeseries.parquet    站级: time, header_pressure_mpa, total_flow_m3_min,
│                                        total_power_kw, station_temperature_c,
│                                        station_humidity_percent, load_rate, load_level, environment_interpolated
├── device_timeseries.parquet     设备级长表: time, device_id, running, state_source,
│                                        discharge_pressure_mpa, flow_m3_min, power_kw, load_ratio,
│                                        specific_power, igv_percent, bov_closed_percent, current_proxy_a,
│                                        vibration_stage_1/2/3_mm_s, discharge_temperature_c,
│                                        runtime_hours, load_hours, maintenance_remaining_hours, fault_code, ...
├── events.parquet                事件长表
├── maintenance.parquet           维护长表
├── forecast_features.parquet     预测特征表 (A3)
├── health_features.parquet       健康特征表 (A3)
├── data_manifest.json            标准化清单+自检
└── feature_manifest.json         特征清单+自检
```

### 3. A3 特征表字段（B/C 若直接用特征）

- 预测特征：`lag_0/1/5/15/30/60/1440`、`roll_{5,15,30,60}_{mean,std,min,max}`、`diff_1/5/15`、
  `hour/weekday/month/is_workday`、`hour_sin/cos`、`weekday_sin/cos`、`ambient_temp_c`、
  `ambient_humidity_pct`、`running_count`、`load_level`、`load_volatility_15`、`target`
- 健康特征：见 `analysis/reports/phaseA3/feature_dictionary.md`

---

## 二、运行时接口在哪（B/C 调用）

两个接口都是**纯函数**，返回约定 JSON，可直接被 MCP Server / 后端包装。

### 1. 用气负荷预测接口

- 代码：`src/air_station/models/forecasting/service.py` → `forecast_json()`
- 返回：

```json
{"status":"success","model_type":"LightGBM","prediction_base_time":"...",
 "predictions":[{"horizon_minutes":15,"target_time":"...","predicted_flow":..,
                 "lower_bound":..,"upper_bound":..}, ...],"elapsed_seconds":1.5}
```

- 样例：`analysis/reports/phaseA4/forecast_interface_sample.json`

### 2. 设备健康诊断接口

- 代码：`src/air_station/models/health/service.py` → `health_json()`
- 返回：

```json
{"status":"success","validation_mode":"unsupervised","evaluation_time":"...",
 "devices":[{"device_id":"4#","health_score":65,"risk_level":"预警","anomaly_score":0.26,
             "top_anomaly_factor":"...","current_value":"...","normal_range":"...",
             "trend_direction":"...","suggestion":"..."}]}
```

- 样例：`analysis/reports/phaseA5/health_interface_sample.json`

---

## 三、离线产物在哪

| 内容 | 路径 |
|------|------|
| 预测指标(MAPE/MAE/RMSE/WAPE+基线+覆盖率) | `artifacts/metrics/forecast_metrics.json` |
| **预测结果(测试集逐点)** | `artifacts/metrics/forecast_test_predictions.parquet`（或 `.csv`） |
| 预测报告 | `analysis/reports/phase2/forecast_report.md` |
| 预测模型 | `artifacts/models/forecast_{15,30,60}m.pkl`（+`.txt`） |
| 预测接口样例 | `analysis/reports/phaseA4/forecast_interface_sample.json` |
| 健康指标 | `artifacts/metrics/health_metrics.json` |
| 健康报告 | `analysis/reports/phaseA5/health_report.md` |
| 健康接口样例 | `analysis/reports/phaseA5/health_interface_sample.json` |
| 阶段报告索引 | `analysis/reports/phase1`, `phase2`, `phaseA1`–`phaseA5` |

`forecast_test_predictions.parquet` 列：`time, horizon_minutes, predicted_flow, lower_bound, upper_bound, actual_flow`（测试集 94,938 行 = 31,646 点 × 3 时域）。

---

## 四、B / C 需要什么，从哪拿

| 角色 | 需要 | 交接物 |
|------|------|--------|
| **B 调度算法** | 未来 15/30/60 分钟用气负荷（点预测+区间） | 调用 `forecast_json()`；或读 `forecast_test_predictions.parquet` |
| **B 调度算法** | 机组当前运行/健康约束 | 调用 `health_json()`（含 `risk_level`、`suggestion`）；设备级输入读 `device_timeseries.parquet` |
| **C 后端** | 预测 MCP 服务 | `forecasting/service.py::forecast_json` + `forecast_metrics.json` |
| **C 后端** | 健康诊断 MCP 服务 | `health/service.py::health_json` + `health_metrics.json` |
| **C 后端** | 数据模型/字段口径 | `analysis/reports/phaseA0/标准字段清单.md`、`接口字段清单.md` |
| **C 后端** | 站级/设备级历史序列 | `data/processed/station_timeseries.parquet`、`device_timeseries.parquet` |

---

## 五、一键重现

```bash
# 依赖
pip install -e .
# 1 校验  2 标准化  3 特征  4 训练+评估  5 健康
python -m air_station.data.pipeline --stage validate --input data
python -m air_station.data.pipeline --stage standardize --input data
python -m air_station.data.pipeline --stage features --input data
python -m air_station.models.forecasting.train && python -m air_station.models.forecasting.evaluate
python -m air_station.models.health.evaluate
```
