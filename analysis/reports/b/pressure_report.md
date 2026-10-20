# B2 管网压力近似模型报告

- 阶段：B2
- 负责人：成员 B
- 分支：`feat/b-optimization-aligned`
- 日期：2026-10-18
- 依据任务书：V1.1 第 6.3 节
- 结论口径：模型仅支持**历史短时持续性**预测；**不声称**具备调度动作后的反事实压力预测能力

---

## 1. 目标与数据

研究站级母管压力 `header_pressure_mpa` 的短时响应，尝试简单可解释模型。

- 数据：`data/processed/station_timeseries.parquet` + 设备运行台数（`device_timeseries` 聚合）。
- 目标：预测**下一分钟**母管压力 `p(t+1)`。
- 特征（仅当前/过去，防止使用预测时刻之后的真实状态）：`p(t)`、`total_flow(t)`、`flow_delta(t)=flow(t)-flow(t-1)`、`running_count(t)`。
- 时间切分同 A：train 2026-03-12~07-31、valid 08-01~08-20、test 08-21~09-12。

样本：train 203,994 / valid 28,788 / test 31,671。

## 2. 候选模型与选型

| 模型 | 验证 MAE | 说明 |
|---|---|---|
| `persistence`（p(t+1)=p(t)） | **0.00212** | 选中 |
| `linear_dynamics`（线性动态回归） | 0.00292 | 验证集未优于持续性 |

两候选验证 RMSE 接近（0.0116 vs 0.0116），MAE 上持续性更优，故冻结为**持续性模型**。

## 3. 冻结模型测试指标（真实运行）

| 段 | MAE(MPa) | RMSE(MPa) | R² | n |
|---|---|---|---|---|
| train | 0.00078 | 0.00585 | 0.976 | 203,994 |
| valid | 0.00212 | 0.01163 | 0.803 | 28,788 |
| **test** | **0.00296** | **0.01005** | 0.951 | 31,671 |

- 残差 std = 0.0101 MPa，`±1.96σ` 不确定性 ≈ **0.0197 MPa**。
- RMSE 明显大于 MAE，说明偶发压力跳变（大残差）主导误差。

## 4. 反事实（调度后）预测能力评估

| 证据 | 值 | 含义 |
|---|---|---|
| 压力 lag-1 自相关 | **0.9866** | 近似随机游走，强持续性 |
| Δp 与流量变化相关 | **−0.0174** | 几乎无关，无法识别调度动作影响 |
| 压力 std | 0.0446 MPa | 波动幅度有限 |
| 落在参考带 [0.55,0.66] 比例 | 0.6545 | 仅约 65% |

**结论**：现有数据**不足以可靠推断调度后的压力变化**。历史压力预测精度（test MAE≈0.003 MPa）主要来自持续性，不能据此宣称具备可靠的控制预测能力。优化后的压力合格率只能作为**仿真指标**，不能作为真实控制效果。

## 5. 压力合格带说明

- 参考带 `[0.55, 0.66] MPa` 为**待确认示例**（CAL-05），非厂家正式安全标准。
- `check_pressure_band()` 仅做区间判断，不作安全承诺。

## 6. B3 可直接调用的接口

```python
from air_station.optimization.pressure import predict_pressure, check_pressure_band

predict_pressure(current_pressure_mpa, *, total_flow_m3_min=None,
                 flow_delta_m3_min=None, running_count=None,
                 model_path="artifacts/models/pressure_model.json") -> dict
# status ∈ {success, INVALID_INPUT, MODEL_UNAVAILABLE}
# success 字段: predicted_pressure_mpa, uncertainty_1.96sigma_mpa, method,
#               in_reference_band, reference_band_mpa, capability, caveats
```

- 返回结构内含 `capability.counterfactual_after_dispatch = "NOT supported by current data"`。
- 缺失特征用训练均值填充，仅作近似；缺失当前压力返回 `INVALID_INPUT`。
- 严格 JSON 可序列化。

## 7. 局限（诚实边界）

1. 只能做历史短时持续性预测，不能预测调度动作对母管压力的因果影响。
2. 参考带为待确认示例，非安全标准。
3. 未建模管网储气/漏损/响应时延；无厂家或工艺参数。
4. 若后续需要反事实压力预测，须引入管网机理模型或专项激励实验数据。

## 8. 复现命令

```bash
python3 -m air_station.optimization.pressure
python3 -m pytest tests/optimization/test_pressure.py -q -rs
```
