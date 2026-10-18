# 阶段 A3 特征字典

| scope | name | unit | description | calculation | missing |
|-------|------|------|-------------|-------------|---------|
| forecast | hour | - | 小时(0-23) | time.hour | 无缺失 |
| forecast | weekday | - | 星期(0=周一) | time.dayofweek | 无缺失 |
| forecast | month | - | 月份 | time.month | 无缺失 |
| forecast | is_workday | - | 是否工作日 | weekday<5 | 无缺失 |
| forecast | hour_sin | - | 小时周期sin | sin(2π·hour/24) | 无缺失 |
| forecast | hour_cos | - | 小时周期cos | cos(2π·hour/24) | 无缺失 |
| forecast | weekday_sin | - | 星期周期sin | sin(2π·weekday/7) | 无缺失 |
| forecast | weekday_cos | - | 星期周期cos | cos(2π·weekday/7) | 无缺失 |
| forecast | lag_k | m3/min | k 分钟前总流量 | target.shift(k) | 保留NaN(树模型) |
| forecast | roll_w_mean | m3/min | 过去 w 分钟均值 | target.shift(1).rolling(w) | 保留NaN |
| forecast | roll_w_std | m3/min | 过去 w 分钟标准差 | 同上.std | 保留NaN |
| forecast | roll_w_min | m3/min | 过去 w 分钟最小 | 同上.min | 保留NaN |
| forecast | roll_w_max | m3/min | 过去 w 分钟最大 | 同上.max | 保留NaN |
| forecast | diff_k | m3/min | k 分钟差分 | base - base.shift(k) | 保留NaN |
| forecast | ambient_temp_c | ℃ | 站房温度 | station_temperature_c | 短缺口已插值 |
| forecast | ambient_humidity_pct | %RH | 站房湿度 | station_humidity_percent | 短缺口已插值 |
| forecast | header_pressure_mpa | MPa | 母管压力 | station.header_pressure_mpa | 保留NaN |
| forecast | running_count | 台 | 运行机组数量 | device.running 按时间求和 | 保留NaN |
| forecast | load_level | - | 历史负荷等级 | station.load_level | 保留NaN |
| forecast | load_volatility_15 | - | 15min 波动率(变异系数) | base.rolling(15).std/mean | 保留NaN |
| health | vib{s}_current | mm/s | 第 s 级振动当前值 | raw | 保留NaN |
| health | vib{s}_mean60 | mm/s | 第 s 级振动过去60min均值 | shift(1).rolling(60).mean | 保留NaN |
| health | vib{s}_max60 | mm/s | 第 s 级振动过去60min最大 | shift(1).rolling(60).max | 保留NaN |
| health | vib{s}_std60 | mm/s | 第 s 级振动过去60min标准差 | shift(1).rolling(60).std | 保留NaN |
| health | vib{s}_delta | mm/s | 第 s 级振动1min变化率 | shift(1)-shift(2) | 保留NaN |
| health | bearing_d_nd_max_diff_c | ℃ | 驱动/非驱动端轴承温差 | |D-ND| | 保留NaN |
| health | coil_max_diff_c | ℃ | 电机三相线圈最大温差 | max(U,V,W)-min(U,V,W) | 保留NaN |
| health | unit_flow_power | kW/(m3/min) | 单位流量功率 | power/flow (flow>eps) | 分母过小置NaN |
| health | trend{N}_{ind} | 指标单位/min | {ind} 过去 N 分钟趋势斜率 | (base-base.shift(N))/N | 保留NaN |
| health | dev_{ind} | - | 相对同负荷区间正常基准的偏差(z) | (x-band_mean)/band_std | 停机置NaN |
