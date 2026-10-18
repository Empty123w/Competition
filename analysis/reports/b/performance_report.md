# B2 机组性能模型报告（功率—流量）

- 阶段：B2
- 负责人：成员 B
- 分支：`feat/b-optimization-aligned`
- 日期：2026-10-18
- 依据任务书：V1.1 第 6.1、6.2 节
- 结论口径：无厂家性能曲线，模型为**工程近似**，`validation_mode=simulation`，不用于真实设备控制

---

## 1. 数据与切分

- 数据源：`data/processed/device_timeseries.parquet`（join 站级环境与母管压力）。
- 时间切分（沿用 A `forecasting.yaml`）：

| 段 | 起止 | 4# 样本 | 5# 样本 |
|---|---|---|---|
| 训练 | 2026-03-12 ~ 07-31 | 126,075 | 165,029 |
| 验证 | 2026-08-01 ~ 08-20 | 28,620 | 27,910 |
| 测试 | 2026-08-21 ~ 09-12 | 31,350 | 31,045 |

- 选型仅在验证集进行；测试集只用于冻结后评估，未反复调参。

## 2. 样本筛选与超额处理

拟合仅使用**运行中且流量落在 [120, 268.9] m3/min** 的样本：

| 类别 | 4# | 5# |
|---|---|---|
| 总行数 | 264,961 | 264,961 |
| 停机 | 46,705 | 7,878 |
| 运行但零流量 | 5,568 | 301 |
| 低于 validated_min(120) | 148 | 2,183 |
| 高于 validated_max(268.9) | 26,492 | 30,611 |
| 非有限/非正功率（剔除） | 1 | 0 |
| 用于拟合 | 186,045 | 223,984 |
| 运行中超额占比 | 12.46% | 11.92% |

- `rated_capacity` = 268.9（铭牌，real）；`validated_operating_max` = 268.9（保守，不主动超额）；
  `observed_max` = 299.7（4#）/ 297.5（5#）（历史瞬时最大，**仅参考，不作为持续安全容量**）。
- 超额观测（>268.9）不参与拟合，单独报告；优化解不建议超过 validated_operating_max。

## 3. 模型比较与选型（验证集 MAE）

候选：线性、二次、单拐点分段、多变量（flow+排气压力+IGV+环境温度）。

| 设备 | linear | poly2 | piecewise | multivar | 选中 |
|---|---|---|---|---|---|
| 4# | 23.63 | 23.34 | 21.97 | **20.08** | multivar |
| 5# | 27.33 | 35.17 | 37.29 | **19.96** | multivar |

单位 kW。两台均选中多变量线性回归，说明排气压力/IGV/环境温度对功率有额外解释力。

## 4. 冻结模型测试指标（真实运行）

| 设备 | 方法 | 段 | MAE(kW) | RMSE(kW) | MAPE(%) | R² | n |
|---|---|---|---|---|---|---|---|
| 4# | 多变量 | train | 10.40 | 15.50 | 0.82 | 0.935 | 126,075 |
| 4# | 多变量 | valid | 13.19 | 16.64 | 1.05 | 0.775 | 28,620 |
| 4# | 多变量 | **test** | **16.50** | **20.74** | **1.27** | 0.609 | 31,350 |
| 5# | 多变量 | train | 11.15 | 16.91 | 0.94 | 0.981 | 165,029 |
| 5# | 多变量 | valid | 11.36 | 16.72 | 1.00 | 0.982 | 27,910 |
| 5# | 多变量 | **test** | **10.94** | **15.00** | **0.85** | 0.804 | 31,045 |

模型参数（节选）：

- 4#：`power ≈ 3.066·flow − 32.56·p − ... + 0.612·igv − 0.839·T_amb + b`
- 5#：`power ≈ 1.198·flow + 136.02·p + 2.775·igv − 4.524·T_amb + b`

## 5. 残差分析

| 设备 | 残差均值(kW) | 残差 std(kW) | |残差| p95(kW) |
|---|---|---|---|
| 4# | **+14.47** | 14.86 | 35.68 |
| 5# | −2.82 | 14.74 | 21.04 |

- **4# 存在约 +15 kW 的正向残差漂移**：测试期内实际功率高于模型预测（真实工况/季节变化）。
  这是单时不变模型的系统性偏差，优化对比时可部分抵消，但绝对值估计偏低约 1.1%。
- 5# 残差均值接近 0，测试泛化良好。
- 4# 训练→测试 R² 明显下降（0.935→0.609），说明存在分布漂移；已在报告中如实保留，未为达标调参。

## 6. 有效运行区间与外推

- 有效区间 `[120, 268.9] m3/min`（拟合范围）。
- `estimate_power()` 对区间外输入返回 `extrapolated=true` 与 `warnings`；
  超过 `validated_operating_max` 时提示「不作为可持续安全容量」。

## 7. B3 可直接调用的接口

```python
from air_station.optimization.performance import estimate_power, estimate_total_power, power_domain

estimate_power(device_id, flow_m3_min, *,
               discharge_pressure_mpa=None, igv_percent=None, ambient_temp_c=None,
               model_path="artifacts/models/power_model.json") -> dict
# status ∈ {success, INVALID_INPUT, MODEL_UNAVAILABLE}
# success 字段: estimated_power_kw, specific_power_kw_per_m3min,
#               in_validated_range, extrapolated, method, model_version, warnings
# flow==0 -> 停机, 功率 0; flow<0/NaN/Inf -> INVALID_INPUT; 模型缺失 -> MODEL_UNAVAILABLE
```

- 逐机独立模型（4#、5# 参数不同），B3 按设备调用，不强行平均。
- 停机零流量返回功率 0，不产生虚假比功率。
- 输出严格 JSON 可序列化（无 NaN/Infinity）。

## 8. 局限（诚实边界）

1. 无厂家性能曲线，模型为数据驱动工程近似，`validation_mode=simulation`。
2. 4# 测试期存在系统性正向偏差（约 +15 kW），跨季节外推需谨慎。
3. 多变量模型含环境温度，存在季节性混杂；对季节外工况的外推能力有限。
4. 未验证功率随 IGV/BOV 的机理关系，仅作回归近似。
5. 超额瞬时流量不作为持续安全容量。

## 9. 复现命令

```bash
python3 -m air_station.optimization.performance
python3 -m pytest tests/optimization/test_performance.py -q -rs
```
