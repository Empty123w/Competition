# 阶段 A6 历史健康评分报告

- 粒度：60 分钟；`validation_mode=unsupervised_rolling`
- 正常基准：同设备同加载区间 `shift(1)+expanding`（评分行不入自身基准）
- 风险等级/规则/偏差缩放取自 `configs/health_thresholds.yaml`
- 停机：`health_score=null, quality_valid=false, quality_reason=not_running`
- 输出行数：8164；有效运行评分行：7053

## 分级分布（有效行）

- 关注: 3100
- 预警: 2660
- 严重: 946
- 正常: 347

> 本表为无监督滚动风险提示，不代表真实故障识别准确率。
