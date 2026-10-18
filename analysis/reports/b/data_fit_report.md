# B0 数据与接口适配检查报告

- 阶段：B0（数据适配与接口验证）
- 负责人：成员 B
- 分支：`feat/b-optimization`
- 检查日期：2026-10-18
- 依据任务书：《B成员人工基线调度优化与喘振预警详细任务书》V1.1（2026-10-18），第 4 节
- 代码基线：本地 `feat/b-optimization`，提交 `471fadd`（已同步 V1.1 任务书）
- 检查对象：A 模块产出的标准数据、预测接口、健康接口，以及历史回测预测文件
- 结论口径：本报告中的数值均为本次实机运行结果；未取得厂家资料的安全边界不作为本次检查结论

---

## 0. 结论摘要

| 检查项 | 结果 | 说明 |
|---|---|---|
| 分支与工作区 | 通过 | 处于 `feat/b-optimization`，工作区干净，与远程同步 |
| Python 环境 | 通过 | Python 3.11.2，满足 `>=3.11`；依赖可导入 |
| 现有测试 | 通过 | `tests/data tests/models` 共 47 passed，0 skipped |
| 标准 Parquet | 通过 | 6 个处理后 Parquet 齐全，行数与时间键一致 |
| 设备级数据完整性 | 有保留项 | 4# 有一段约 3.9 天遥测空档，见 §4.6 |
| `forecast_json()` | 通过 | 返回 15/30/60 分钟点预测与区间，可 JSON 序列化 |
| `health_json()` | 通过 | 返回 4#/5# 无监督风险等级与建议 |
| 历史回测预测对齐 | 通过 | `actual_flow` 与 `station@(time+h)` 完全对齐（max diff 0） |
| 可直接支撑 B 建模 | 是 | 具备运行状态、流量、功率、压力、IGV/BOV、质量标记 |
| 安全边界数据 | 缺失 | 无厂家喘振曲线/性能曲线，须以工程假设建模，见 `assumptions_register.md` |

---

## 1. 环境与依赖

| 项 | 值 |
|---|---|
| Python | 3.11.2（`/usr/bin/python3`，满足 `pyproject.toml` 的 `>=3.11`） |
| pandas | 3.0.6 |
| numpy | 2.4.6 |
| pyarrow | 26.0.0 |
| scikit-learn | 1.9.1 |
| lightgbm | 4.7.0 |
| pytest | 9.1.1 |
| 安装方式 | `pip install --break-system-packages -e ".[dev]"`（含 `pytest`、`python-docx`） |

导入自检通过：`import air_station, pandas, pyarrow, sklearn, lightgbm, yaml, joblib`。

> 说明：本环境 pandas 3.0 / numpy 2.4 为较新大版本，A 模块测试在该版本下仍全量通过，B 模块开发沿用同一环境。

## 2. 现有测试结果（真实运行）

```
python -m pytest tests/data tests/models -q
47 passed in 95.04s
```

与 README 声明的交付基准「47 passed，0 skipped」一致，无 skip，说明 Parquet、模型与依赖均完整。

## 3. 标准数据文件概览

路径：`data/processed/`

| 文件 | 行数 | 主键 | 时间范围 |
|---|---|---|---|
| `station_timeseries.parquet` | 264,961 | `time` | 2026-03-12 00:00 ~ 2026-09-12 00:00 |
| `device_timeseries.parquet` | 529,922（=2×264,961） | `time` + `device_id` | 同上 |
| `events.parquet` | 529,922 | `time` + `device_id` | 同上 |
| `maintenance.parquet` | 529,922 | `time` + `device_id` | 同上 |
| `forecast_features.parquet` | — | `time` | 同上 |
| `health_features.parquet` | — | 长表 | 同上 |

- 时间粒度：1 分钟；样本数 264,961 与 `configs/data_quality.yaml` 的 `n_rows_expected` 一致。
- 设备：`device_id ∈ {4, 5}`，`device_type = centrifugal`，每台各 264,961 行。
- 站级表无缺失列；`header_pressure_mpa` 等字段统计与 §4 一致。
- **设备编号确认**：真实设备固定为 4#、5# 两台，全库不存在其他设备编号；3# 机不出现在真实数据中，仅可用于独立仿真场景（对应 V1.1 §2.2、§14.4-1）。
- **时区状态**：`time` 为无时区本地时间，是否等同北京时间**待与 A/C 确认**（V1.1 §2.3-1）。

### 3.1 快照哈希（V1.1 §4.5，固定评测输入）

以 `sha256sum` 记录本次输入快照，后续基线/优化回测须引用同一哈希：

| 文件 | sha256（前 16 位） |
|---|---|
| `data/processed/station_timeseries.parquet` | `804a3496129e43fc` |
| `data/processed/device_timeseries.parquet` | `eddf7a916b0be7ed` |
| `data/processed/events.parquet` | `e7a2e28449420bf5` |
| `data/processed/maintenance.parquet` | `bd64de53e91dfc69` |
| `data/processed/forecast_features.parquet` | `ad1a02bab11d9455` |
| `data/processed/health_features.parquet` | `b3d2741529c82157` |
| `artifacts/metrics/forecast_test_predictions.parquet` | `1bc12f435f19205a` |
| `artifacts/models/forecast_15m.pkl` | `b3f9b2ac103b54e0` |
| `artifacts/models/forecast_30m.pkl` | `fdc739833c869a94` |
| `artifacts/models/forecast_60m.pkl` | `ce6ef1f766e3a4a7` |

随机种子沿用 A 预测配置的 `random_state: 42`；B 新增随机性（如抽样/仿真）须另记录种子。

## 4. 4# / 5# 空压机运行状态分析

数据来源：`data/processed/device_timeseries.parquet`（长表），统计均以「运行中」行（`running == True`）为主。

### 4.1 运行状态

| 指标 | 4# | 5# |
|---|---|---|
| 运行行数 | 218,256 | 257,083 |
| 运行时间占比 | 82.4% | 97.0% |
| 停机行数 | 46,705 | 7,878 |
| `state_source = event_record` | 264,858 | 264,810 |
| `state_source = power_fallback` | 103 | 151 |

运行状态以事件表状态码（8=开启，10=关闭）优先，功率兜底判定，来源标记清晰。

站级同时运行台数分布：

| 同时运行台数 | 行数 | 占比 |
|---|---|---|
| 2 台 | 217,189 | 82.0% |
| 1 台 | 40,961 | 15.5% |
| 0 台 | 6,811 | 2.6% |

**启停转换统计**（按 `running` 0/1 翻转）：

| 指标 | 4# | 5# |
|---|---|---|
| 翻转次数 | 42 | 34 |
| 启动次数 | 21 | 17 |
| 停机次数 | 21 | 17 |
| 连续段数 | 43 | 35 |
| 段时长 中位数（min） | 488 | 547 |
| 短段（<5 min）数 | 12 | 9 |

- 全期真实启停极少（各约 20 次），说明两台机基本长期连续运行，历史以「加载/卸载」而非「启停」调节为主。
- 12/9 个 <5 min 的短段与 §4.7 的遥测毛刺同步，属数据噪声而非真实启停，B1 基线评估须先剔除后再统计启停次数。

### 4.2 流量 / 功率 / 压力（运行中）

| 指标（运行中） | 4# p01 / p50 / p99 | 5# p01 / p50 / p99 | 单位 |
|---|---|---|---|
| 实时流量 `flow_m3_min` | 0 / 244.4 / 288.8 | 121.5 / 250.0 / 288.0 | m3/min |
| 有功功率 `power_kw` | 1116 / 1307 / 1467 | 1020 / 1315 / 1466 | kW |
| 排气压力 `discharge_pressure_mpa` | 0 / 0.58 / 0.65 | 0.51 / 0.60 / 0.66 | MPa |
| 一级振动 `vibration_stage_1_mm_s` | — | — | mm/s |

- 两台机额定：1250 kW、268.9 m3/min、0.66 MPa（`设备档案.json`）。
- 排气压力中位数 5# 略高于 4#（0.60 vs 0.58 MPa）。

环境工况（站级，V1.1 §4.2 要求）：

| 字段 | min / p01 / p50 / p99 / max | 单位 |
|---|---|---|
| `station_temperature_c` | 0.01 / 13.6 / 31.2 / 43.6 / 45.9 | ℃ |
| `station_humidity_percent` | 0.01 / 16.6 / 44.1 / 68.1 / 74.8 | %RH |
| `header_pressure_mpa` | 0 / 0.48 / 0.57 / 0.64 / 0.71 | MPa |

- 环境温湿度存在 0 值掉点（A 已用前值填充并置 `environment_interpolated`），B 拟合性能模型时按 `environment_interpolated == False` 优先取用。

### 4.3 加载率与超额运行

`load_ratio` 经校验等于 `flow_m3_min / 268.9`（额定流量），相关系数 1.0、平均绝对差 0.0，定义明确。

| 指标 | 4# | 5# |
|---|---|---|
| 运行中 `load_ratio` 中位数 | 0.909 | 0.930 |
| 运行中 `load_ratio > 1` 占比 | 12.14% | 11.91% |
| 运行中最大 `load_ratio` | 1.115 | 1.106 |
| 运行中最大流量 | 299.7 | 297.5 m3/min |

- 站级 `load_rate > 1` 行数 26,603（占 10.0%），最大 1.11。
- **超额运行确实存在**（流量超过额定流量，最高约 111.5%），且两机占比接近，属真实工况特征而非单点异常。B 的性能模型与约束必须兼容「接近/略超额定」的运行区。

### 4.4 IGV / BOV 调节行为

| 指标 | 4# | 5# |
|---|---|---|
| `igv_percent` 运行中中位数 | 100 | 100 |
| `igv_percent == 100` 占比（运行中） | 86.95% | 64.59% |
| `igv_percent < 100` 占比（运行中） | — | 35.4% |
| `igv_percent` 运行中 p01 | 0 | 34.0 |
| `bov_closed_percent` 运行中中位数 | 100 | 100 |

按流量分档观察调节行为（运行中，flow > 10）：

- 低流量段（约 150~200 m3/min）：IGV ≈ 38~40%，BOV 部分开启（闭度 78~92%）；
- 高流量段（约 232~280 m3/min）：IGV → 85~100%，BOV 全关（100%）；
- 5# 在 190→232 m3/min 之间存在明显阶跃（IGV 39→85、功率 1052→1251 kW），呈典型加/卸载台阶；
- 4# 长期停留在高流量段（`igv==100` 占 87%），5# 承担更多低流量调节（`igv<100` 占 35%）。

**结论**：5# 为调峰/调节机（IGV 可下探至约 34%），4# 多为基载机。这是 B 基线还原与优化分配的重要观测依据。

### 4.5 比功率与能效趋势

`specific_power` 经校验等于 `power_kw / flow_m3_min`（相关系数 1.0）。

| 流量段（4# 运行中） | 平均流量 | 平均比功率 kW/(m3/min) |
|---|---|---|
| (200,240] | 229.9 | 5.46 |
| (240,260] | 250.7 | 5.29 |
| (260,270] | 264.7 | 5.20 |
| (270,∞) | 279.0 | 5.11 |

- 比功率随流量升高而下降，高负荷更高效；低流量段（<150）比功率升至 8.5 以上。
- 能量口径：全期站级总电耗约 10.25 GWh；运行态单机能耗 4# 约 0.0911、5# 约 0.0898 kWh/m3。

### 4.6 数据质量标记

`quality_valid` 全为 True；`quality_reason` 分布：

| quality_reason | 行数 | 含义 |
|---|---|---|
| `ok` | 470,656 | 正常 |
| `weak_fault_label` | 36,147 | 故障码 26 弱标签窗口 |
| `long_gap_temperature` | 17,280 | 前 6 天冷却器温度长缺失 |
| `maintenance_sentinel` | 5,585 | 维护计数器哨兵剔除 |
| `state_fallback`（含组合） | 254 | 运行状态功率兜底 |

其他标记：

- `temperature_available = False`：17,280 行（2026-03-12~03-17 前 6 天）；
- `maintenance_sentinel_removed = True`：5,587 行；
- `fault_label_quality = weak` 且 `possible_station_level_event = True`：36,183 行；
- 故障码分布：4# 故障码 26 共 19,499 行（2026-03-19~08-09），5# 共 16,670 行（2026-03-19~04-13）；另有 4#/5# 各 8 行极小码值（13、6.5、3.25、1.62、0.81），数量极少，疑为边界/插值噪声。

### 4.7 数据缺失与异常清单

| 编号 | 现象 | 位置/规模 | 对 B 的影响 | 处理建议 |
|---|---|---|---|---|
| D1 | 4# 遥测空档 | `runtime_hours`/`load_hours`/`maintenance_remaining_hours` 整段为 NaN，2026-07-22 15:02 ~ 07-26 12:04，5,583 行 | 该段无法评估 4# 保养剩余与负载小时 | B 不用维护计数做硬约束，仅作参考 |
| D2 | 运行中流量为 0 | 4# 5,568 行（56 段）、5# 301 行 | 比功率在该处被放大为异常大值 | 建模前以 `flow<=0` 掩码剔除 |
| D3 | `specific_power` 极端离群 | >20 共 447 行（4# 32、5# 415），最大约 12.7 万 | 破坏性能曲线拟合 | 以流量下界过滤后重建 |
| D4 | `current_proxy_a` 尖峰 | >200 共 2,378 行（4# 2,284、5# 94），最大约 1000 A | 电流非核心特征，影响有限 | 建模时做鲁棒截断或忽略 |
| D5 | 站级压力为 0 | 506 行（51 段），多与 D2 同步 | 压力约束统计被拉低 | 压力合规统计剔除 0 值 |
| D6 | 停机但功率>1 kW | 4# 2,316 行、5# 98 行 | 运行状态与功率存在少量不一致 | 以 `running` 为准，记录不一致率 |
| D7 | 故障码 26 弱标签 | 4# 19,499 / 5# 16,670 行，两机高度同步 | 不可当独立故障真值 | 健康限载仅作软提示 |

## 5. 接口验证

### 5.1 预测接口 `forecast_json()`

调用成功，实测输出：

| horizon_minutes | target_time | predicted_flow | lower_bound | upper_bound |
|---|---|---|---|---|
| 15 | 2026-09-12T00:15:00 | 499.18 | 483.75 | 512.86 |
| 30 | 2026-09-12T00:30:00 | 498.49 | 479.85 | 516.78 |
| 60 | 2026-09-12T01:00:00 | 500.39 | 479.93 | 523.21 |

- 顶层字段：`status, model_type, prediction_base_time, predictions, elapsed_seconds`；
- `prediction_base_time` 取 `forecast_features.parquet` 最后一行（2026-09-12T00:00:00），即仅支持「最新时刻」的在线预测；
- 区间单调（lower ≤ point ≤ upper），可 `json.dumps(..., allow_nan=False)`。

**对 B 的约束**：在线预测只能给「数据末端之后」的 15/30/60 分钟；不能指定任意历史时刻。历史时刻的预测须用 §6 的回测文件。

### 5.2 健康接口 `health_json()`

调用成功，实测输出：

| device_id | health_score | risk_level | anomaly_score | top_anomaly_factor | 建议 |
|---|---|---|---|---|---|
| 4# | 65 | 预警 | 0.26 | 排气温度偏离正常范围 | 检查冷却水系统与润滑油，必要时降载 |
| 5# | 63 | 预警 | 0.59 | 排气温度偏离正常范围 | 检查冷却水系统与润滑油，必要时降载 |

- `validation_mode = unsupervised`，`evaluation_time = 2026-09-11T16:00:00Z`（=本地 09-12 00:00 的 UTC）；
- 设备顺序稳定为 4#、5#；字段名与 A→C 文档一致。
- 风险等级为无监督提示，**不可作为真实故障定论或直接停机依据**。

## 6. 历史回测预测文件与对齐

文件：`artifacts/metrics/forecast_test_predictions.parquet`（另有 `.csv`）

| 项 | 值 |
|---|---|
| 行数 | 94,938 |
| 列 | `time, horizon_minutes, predicted_flow, lower_bound, upper_bound, actual_flow` |
| 时间范围 | 2026-08-21 00:00 ~ 2026-09-11 23:45 |
| 时域 | 15 / 30 / 60 分钟 |
| 每秒域行数 | 15m:31,666；30m:31,651；60m:31,621 |
| 缺失 | 无 |

**对齐验证**：逐时域把 `actual_flow` 与 `station_timeseries` 在 `time + horizon_minutes` 的总流量比对：

| 时域 | `actual_flow` vs `station@(time+h)` 最大差 | 平均差 |
|---|---|---|
| 15 min | 0.0000 | 0.000005 |
| 30 min | 0.0000 | 0.000005 |
| 60 min | 0.0000 | 0.000005 |

**结论**：该文件完全支持「按历史时刻对齐预测与运行数据」。字段含义为：在基准时刻 `time` 发出的预测，其真值对应目标时刻 `time + horizon_minutes` 的站级总流量。B 的基线回测与优化回测统一采用此对齐口径。每小时域行数略有差异是因为末尾超出测试区间的目标点被剔除。

## 7. 时间切分表（V1.1 §4.4）

沿用 A 预测模块的切分（`configs/forecasting.yaml`），B 不重复调参、不在最终测试段反复调阈值：

| 段 | 起止 | 用途 | 备注 |
|---|---|---|---|
| 训练/拟合 | 2026-03-12 ~ 2026-07-31 | 拟合人工规则阈值、性能/压力模型 | 启停行为样本极少（各约 20 次），需更长窗口观察 |
| 验证/调优 | 2026-08-01 ~ 2026-08-20 | 规则与模型选择、参数敏感性 | 不得用于最终结论 |
| 最终评估 | 2026-08-21 ~ 2026-09-12 | 冻结回测（对应 `forecast_test_predictions`） | 阈值与规则在此段前冻结 |

- 冻结测试预测表窗口为 2026-08-21 00:00 ~ 2026-09-11 23:45，与最终评估段一致。
- 若评估窗口外需回测，须另请 A 导出同口径预测（V1.1 §2.1）。
- 回放严格遵守 `as_of` 边界：每个时刻只使用该时刻之前可获得的数据（V1.1 §4.5）。

## 8. 基线与仿真场景清单（V1.1 §4、§9.2）

固定窗口（至少八类，用于基线与优化同工况对比）：

| 场景 ID | 类型 | 说明 | 来源标记 |
|---|---|---|---|
| S-low | 低负荷 | 单机可满足的谷段 | historical_measurement |
| S-peak | 峰值负荷 | 接近总额定气量 | historical_measurement |
| S-ramp-up | 快速上升 | 负荷陡增窗口 | historical_measurement |
| S-ramp-down | 快速下降 | 负荷陡降窗口 | historical_measurement |
| S-single-fault | 单机异常 | 4# 或 5# 健康限载 | 仿真（健康限载，见 §7 假设） |
| S-high-surge | 高喘振 | 代理边界高风险 | simulation |
| S-capacity-short | 单机容量不足 | 一机无法满足需求 | simulation |
| S-missing-data | 数据缺失 | 关键传感/预测缺失 | simulation |

固定 fixture（存放于 `data/fixtures/b/`，V1.1 §3.1）：`normal.json`、`load_drop.json`、`health_derate.json`、`infeasible.json`。

- 健康限载在缺少逐时刻评分前只能标 `simulation`，不得用最新 `health_json()` 回填历史（V1.1 §2.1、§3.2）。
- 3# 机仅用于独立模拟三机场景，不混入真实统计（V1.1 §2.2、§14.4-1）。

## 9. 对 B 建模的适用性判断

可直接支撑：

- 运行状态、流量、功率、排气压力、IGV、BOV、比功率、质量标记、故障弱标签；
- 站级母管压力、总流量、总功率，用于压力合规与能耗核算；
- 历史逐点预测与真值，用于回测评估。

需要工程假设支撑（无实测/厂家来源，详见 `assumptions_register.md`）：

- 喘振边界曲线、最低稳定流量、压力目标带、健康限载映射、启停时间与成本、加卸载滞环、攀升速率。

须在回测中显式剔除或降权处理的样本：

- D2/D3/D5 异常点（运行但流量为 0、比功率离群、压力为 0）；
- 前 6 天冷却器温度长缺失段；
- 4# 维护计数空档段。

## 10. 本次检查未改动任何 A 模块产物

本阶段仅读取数据与调用接口，未修改 `data/`、`artifacts/`、`configs/` 及 A 的源码。

## 11. V1.1 §4 验收对照

| V1.1 §4 验收项 | 本报告对应 | 结论 |
|---|---|---|
| 无单位争议 | §4.2、§4.3、§4.5 均标注单位，`load_ratio`/`specific_power` 定义已校验 | 通过 |
| 无设备编号混乱 | §3 固定 4#/5#，3# 仅仿真 | 通过 |
| 明确哪些约束有设备资料支撑 | §12 与 `assumptions_register.md` 来源列 | 通过 |
| 哪些只是演示假设 | `assumptions_register.md` 的 `engineering`/`simulation` | 通过 |
| 最终评估数据未参与模型/阈值选择 | §7 时间切分表 | 通过 |

## 12. 约束来源分类（设备资料 vs 演示假设）

| 约束 | 来源 | 是否设备资料支撑 |
|---|---|---|
| 额定功率/流量/压力 | `设备档案.json` | 是（real） |
| 流量、功率、压力、IGV/BOV 实测区间 | 标准数据 | 是（real/inferred） |
| 比功率随流量趋势 | 实测拟合 | 是（inferred） |
| 母管压力合格带 | 待确认示例（0.55~0.66 MPa） | 否（示例，待甲方） |
| 备用容量定义（额外/N-1） | 待确认 | 否（待 A/C 冻结） |
| 加载率目标（±10% 口径） | 待确认 | 否（待冻结） |
| 最短运行/停机时长、启停次数上限 | 工程假设 | 否（engineering） |
| 健康限载系数 | 工程假设 | 否（engineering） |
| 喘振代理边界与门槛 | 仿真假设 | 否（simulation） |
