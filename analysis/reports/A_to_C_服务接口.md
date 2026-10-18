# A → C 服务接口（后端成员）

面向后端（C）。A 提供**两个运行时函数**作为唯一接口来源；C 只做封装（MCP/API），
**不复制 A 的训练/特征代码**，**不得修改成功响应的字段名与层级**。

---

## 1. 导入与签名

```python
from air_station.models.forecasting.service import forecast_json
from air_station.models.health.service import health_json

def forecast_json(processed_dir: str = "data/processed",
                  config_path: str = "configs/forecasting.yaml",
                  model_dir: str = "artifacts/models",
                  horizons: list[int] | None = None) -> dict: ...

def health_json(processed_dir: str = "data/processed",
                thresholds_path: str = "configs/health_thresholds.yaml") -> dict: ...
```

## 2. 正常调用示例

```python
import json
from air_station.models.forecasting.service import forecast_json
from air_station.models.health.service import health_json

fc = forecast_json()                       # 默认 15/30/60 分钟
print(json.dumps(fc, ensure_ascii=False, allow_nan=False))

h = health_json()                          # 4# / 5#
print(json.dumps(h, ensure_ascii=False, allow_nan=False))
```

## 3. 成功 JSON 示例

### 预测 `forecast_json`

```json
{
  "status": "success",
  "model_type": "LightGBM",
  "prediction_base_time": "2026-09-12T00:00:00",
  "predictions": [
    {"horizon_minutes": 15, "target_time": "2026-09-12T00:15:00",
     "predicted_flow": 499.18, "lower_bound": 483.75, "upper_bound": 512.86}
  ],
  "elapsed_seconds": 1.43
}
```

- 顶层字段固定：`status, model_type, prediction_base_time, predictions, elapsed_seconds`
- 每个预测项固定：`horizon_minutes, target_time, predicted_flow, lower_bound, upper_bound`
- `lower_bound <= predicted_flow <= upper_bound`；可 `json.dumps(..., allow_nan=False)`

### 健康 `health_json`

```json
{
  "status": "success",
  "validation_mode": "unsupervised",
  "evaluation_time": "2026-09-11T16:00:00Z",
  "devices": [
    {"device_id": "4#", "health_score": 65, "risk_level": "预警", "anomaly_score": 0.26,
     "top_anomaly_factor": "排气温度偏离正常范围", "current_value": "108.00 ℃",
     "normal_range": "90.0 ~ 105.0 ℃", "trend_direction": "平稳",
     "suggestion": "建议检查冷却水系统与润滑油, 必要时降载运行"}
  ]
}
```

- 顶层字段**必须且只能**：`status, validation_mode, evaluation_time, devices`
- 每个设备**必须且只能** 9 字段：`device_id, health_score, risk_level, anomaly_score,
  top_anomaly_factor, current_value, normal_range, trend_direction, suggestion`
- `evaluation_time` 为 UTC ISO 8601（`Z` 结尾）；本地无时区时间按 `Asia/Shanghai` 解释后转 UTC
- `devices` 稳定排序为 `4#`、`5#`
- `trend_direction ∈ {平稳, 改善, 恶化}`；`current_value`/`normal_range` 不适用时为 `"N/A"`
- 无 `model_version`/调试字段等额外键

## 4. 必需输入文件与配置

| 用途 | 文件 |
|------|------|
| 预测特征 | `data/processed/forecast_features.parquet` |
| 预测配置 | `configs/forecasting.yaml` |
| 预测模型 | `artifacts/models/forecast_{15,30,60}m.pkl` |
| 健康特征 | `data/processed/health_features.parquet` |
| 健康阈值 | `configs/health_thresholds.yaml` |

## 5. 模型文件位置与命名

```
artifacts/models/
├── forecast_15m.pkl   # joblib: {model, model_type, horizon, feature_cols, ci_lower, ci_upper}
├── forecast_30m.pkl
├── forecast_60m.pkl
├── forecast_15m.txt   # LightGBM 原生文本模型副本(可选)
├── forecast_30m.txt
├── forecast_60m.txt
└── forecast_meta.json
```

`.pkl` 为运行时加载格式；`.txt` 是 LightGBM 原生文本副本。加载 `.pkl` 需要 `lightgbm`
（已在 `pyproject.toml` 声明，`pip install -e .` 即可）。

## 6. 异常行为（C 需捕获并转成服务层错误，禁止返回假成功）

| 场景 | 异常 | 消息要点 |
|------|------|----------|
| 特征文件缺失 | `FileNotFoundError` | 路径 + “先运行 data.pipeline --stage features” |
| 特征文件为空 | `ValueError` | 路径为空 |
| 不支持的时域 | `ValueError` | 请求时域 + 支持时域 + 缺失模型路径 |
| 模型文件缺失 | `FileNotFoundError` | 模型路径 + “先运行 train” |
| 模型损坏/依赖缺失 | `RuntimeError` | 模型路径 + 原因 |
| 预测值非有限 | `RuntimeError` | 时域 + NaN/Inf 提示 |
| 配置文件缺失 | `FileNotFoundError` | 配置路径 |
| 健康特征缺失/为空 | `FileNotFoundError`/`ValueError` | 路径 |
| 健康阈值配置缺失 | `FileNotFoundError` | 阈值路径 |

## 7. C 的职责边界

- C 只**封装**上述两个函数为 MCP / REST；不复制训练、特征工程、清洗代码；
- C 的 API/MCP 层**不得修改**成功响应的字段名与层级；
- 健康接口失败时，由 C 转换为服务层错误响应，**不得伪造 `status: success`**；
- C 不直接下发设备控制指令。
