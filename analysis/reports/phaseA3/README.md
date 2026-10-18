# 阶段 A3：特征工程 — 说明与验收

- 运行：`python -m air_station.data.pipeline --stage features --input data`
- 产出：
  - `data/processed/forecast_features.parquet`（264,961 × 46）
  - `data/processed/health_features.parquet`（529,922 × 64）
  - `analysis/reports/phaseA3/feature_dictionary.{csv,md}`（名称/单位/说明/计算/缺失策略）

## 1. 负荷预测特征

时间（hour/weekday/month/is_workday）、周期 sin/cos（hour/weekday）、
滞后 lag_1/5/15/30/60/1440、滚动 roll_{5,15,30,60}_{mean,std,min,max}、
差分 diff_1/5/15、环境 ambient_temp_c/humidity_pct、母管压力、
运行机组数 running_count、load_level、load_volatility_15。

## 2. 健康诊断特征

三级振动的当前/60min 均值·最大·标准差/变化率；轴承温度；三相线圈温度及最大温差
`coil_max_diff_c`；齿轮箱供油温度；油箱油温；排气温度；冷却水温度；变速箱油压；
油滤器压差；空气过滤器压降；代理电流 `current_proxy_a`；功率/流量/加载率；
单位流量功率 `unit_flow_power`；相对同负荷区间基准的偏差 `dev_*`；
15/60/240 分钟趋势斜率 `trend{N}_{ind}`。

## 3. 防泄漏措施（红线）

| 措施 | 实现 |
|------|------|
| 严格分组 | 健康特征按 `device_id` 分组计算 |
| 滞后仅用过去 | `lag_k = target.shift(k)` |
| 滚动不含当前 | 先 `base = series.shift(1)` 再 `rolling()` |
| 差分用过去 | `base - base.shift(k)` |
| 目标不入特征 | 特征列排除 `target` 与其源列 |

## 4. 工况分离与基准线

- 停机行不参与基准拟合，且 `dev_*` 一律置 **NaN**（不填 0）；
- 基准按**加载率分层**（low<0.3 / mid / high>0.7）在健康基线期（03-12~08-20）统计
  `(x-band_mean)/band_std`，避免正常启停双峰被误判。

## 5. 无穷值与缺失值

- 除法（单位流量功率、变化率）加除零保护（|分母|≤0.1 输出 NaN）；
- 生成后统一 `inf/-inf → NaN`；
- 缺失策略：树模型（LightGBM/XGBoost）**保留 NaN** 自行分裂；神经网络需先按列
  前向填充或均值填充（本阶段不填充，交由模型层决定）。

## 6. 验收自检

| 标准 | 结果 |
|------|------|
| 特征不含未来信息 | PASS（单测验证滚动/滞后） |
| 同输入重复生成一致 | PASS（`test_*_idempotent` + 真实数据 `equals=True`） |
| 每特征有名称/单位/说明 | PASS（`feature_dictionary`） |
| 停机不污染健康模型 | PASS（`dev_*` 停机为 NaN） |
| 无无穷值 | PASS（`*_no_inf`） |
| 缺失处理有记录 | PASS（字典 `missing_strategy` 列） |
