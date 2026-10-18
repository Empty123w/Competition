# 阶段 A5：设备健康诊断 — 说明与接口

- 运行：`python -m air_station.models.health.evaluate`（生成指标与报告）
- 接口样例：`health_interface_sample.json`（本目录）

## 1. 运行时健康诊断接口（供 C 后端 / B 调度，字段名固定）

```json
{
  "status": "success",
  "validation_mode": "unsupervised",
  "evaluation_time": "2026-09-12T00:00:00",
  "devices": [
    {
      "device_id": "4#",
      "health_score": 65,
      "risk_level": "预警",
      "anomaly_score": 0.26,
      "top_anomaly_factor": "排气温度偏离正常范围",
      "current_value": "108.00 ℃",
      "normal_range": "90.0 ~ 105.0 ℃",
      "trend_direction": "平稳",
      "suggestion": "建议检查冷却水系统与润滑油, 必要时降载运行"
    }
  ]
}
```

调用：`air_station.models.health.service.health_json()`。

## 2. 输出路径（规范）

| 内容 | 路径 |
|------|------|
| 评估指标 | `artifacts/metrics/health_metrics.json` |
| 诊断报告 | `analysis/reports/phaseA5/health_report.md` |
| 接口样例 | `analysis/reports/phaseA5/health_interface_sample.json` |

## 3. 强制约束落实

| 约束 | 实现 |
|------|------|
| 阈值外部化 | 全部阈值在 `configs/health_thresholds.yaml`，代码零硬编码 |
| 数据源复用 | 直读 `data/processed/health_features.parquet`，不重算特征 |
| 停机过滤前置 | `_load_features` 加载后第一步 `df[running==1]` |

## 4. 方法

- 健康分 = 100 × (1 − (0.5×专家规则扣分 + 0.5×相对偏差异常分))；
- 专家规则：当前值对 `warn/alarm` 软阈值扣分（阈值取自运行工况分位数标定）；
- 异常分：`dev_*`（相对同负荷区间正常基准）按 `dev_scale` 归一化，取最大偏离；
- 风险分级：`正常/关注/预警/严重`（阈值在配置中，可调）。

## 5. 局限（如实记录）

- 无多类故障标签与 RUL 标签，属**无监督**结果，需人工/机理复核；
- 与运行事件记录中的故障码 26 弱标签窗口对比，**分离度为负**（故障期健康分反而略高），
  与前期判断一致：该"故障"两机高度同步、疑似**站级共因事件**，并不表现为机组参数劣化，
  故不能作为独立的机组故障真值；
- 喘振相关诊断需结合喘振边界（当前缺失），不在本阶段。
