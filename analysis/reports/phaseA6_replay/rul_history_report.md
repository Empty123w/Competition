# 阶段 A6 历史 RUL 迁移估计报告

> 模拟退化模型在真实历史遥测上的**迁移估计**，用于软约束演示，**不代表真实剩余寿命**。

- validation_mode: simulation；usage_context: real_telemetry_transfer
- 粒度：6 小时；窗口 (T-6h, T]；需 8 个完整历史节点
- 输出行数：1252；有效：252

## 质量原因分布

- out_of_distribution: 998
- ok: 252
- window_insufficient: 2

## B 使用限制

- 仅作健康惩罚/调度软约束；不触发停机，不放宽任何安全约束；过期或无效时回退健康等级。
