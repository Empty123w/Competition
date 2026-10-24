
## 13. 维护预警与维护指标（A7，混合仿真口径）

- **运行时接口**：`air_station.models.rul.maintenance_sim.maintenance_json()`，返回两台设备的趋势分数与 `warning`/`risk_level`/`suggestion`；`validation_mode="hybrid_simulation"`。
- **离线指标**：`artifacts/metrics/maintenance_metrics.json`（非计划停机下降、MTBF 提升、异常预警准确率、预警提前），报告 `analysis/reports/phaseA7_maint/maintenance_report.md`。
- 口径：**真实遥测作底 + 注入退化标签**；预警基于振动/温度/电流/能效趋势分数。
- B 使用建议：把 `warning=true` 的机组作为**计划维护/降载**的软信号；它不触发停机，过期或无效时回退到健康等级。
- 说明：结果属**混合仿真自证**，B 不得表述为现场实测停机/MTBF。
