---
knowledge_id: KB-MAINT-001
title: RUL 与预测性维护（混合仿真）
category: 健康与维护
keywords:
  - RUL
  - 剩余寿命
  - 预测性维护
  - 非计划停机
  - MTBF
  - 异常预警
  - 提前预警
  - hybrid_simulation
  - 注入退化
  - maintenance_json
metric_refs:
  - { label: "非计划停机下降(%)", path: "artifacts/metrics/maintenance_metrics.json", json_path: "unplanned_stops.reduction_pct" }
  - { label: "MTBF提升(%)", path: "artifacts/metrics/maintenance_metrics.json", json_path: "mtbf_hours.improvement_pct" }
  - { label: "预警准确率", path: "artifacts/metrics/maintenance_metrics.json", json_path: "warning_accuracy.accuracy" }
  - { label: "预警提前中位(h)", path: "artifacts/metrics/maintenance_metrics.json", json_path: "warning_lead_hours.median" }
  - { label: "RUL MAE(h)", path: "artifacts/metrics/rul_metrics.json", json_path: "metrics.MAE" }
source_paths:
  - configs/rul.yaml
  - configs/maintenance_policy.yaml
  - src/air_station/models/rul/service.py
  - src/air_station/models/rul/maintenance_sim.py
origin: project_repository
validation_mode: hybrid_simulation
last_verified: 2026-10-24
---

## 结论

RUL（剩余寿命）由**模拟退化数据**训练的分位数 GBR 给出，标 `validation_mode=simulation`，仅作调度软约束。预测性维护在**真实遥测作底 + 注入退化标签**的混合仿真下，评估停机下降、MTBF、预警准确率与提前时间。

## 输入

- RUL：真实健康特征（振动/温度/电流/能效）滚动窗口；模型输入列见 `rul_meta.json`。
- 维护预警：真实遥测 6 小时节点的滚动趋势分数（相对本段早期基准）。

## 输出

- `rul_json()`：`estimated_rul_hours / lower_bound_hours / upper_bound_hours / method`，`validation_mode=simulation`。
- `maintenance_json()`：`trend_score / warning / risk_level / top_trend_factor / suggestion`，`validation_mode=hybrid_simulation`。

## 方法或规则

- 预警基于振动/温度/电流/能效的滚动趋势；阈值由标准网格选取，需满足准确率≥90% 且提前中位≥24h。
- 标签来源为**注入退化**（`label_source=injected_degradation`），不是现场实测。

## 使用方式

```python
from air_station.models.rul.service import rul_json
from air_station.models.rul.maintenance_sim import maintenance_json
```

## 证据与来源

- `configs/rul.yaml`、`configs/maintenance_policy.yaml`、`artifacts/metrics/{rul_metrics,maintenance_metrics}.json`
- 报告：`analysis/reports/phaseA7_maint/maintenance_report.md`

## 适用范围和限制

**不得**把模拟/混合仿真结果称作现场效果；真实停机/维修台账缺失时按赛题许可用注入退化，属自证。
