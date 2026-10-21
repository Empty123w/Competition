# GRU 喘振预警优化 (第三阶段; validation_mode=simulation)

- dev/valid/test 事件数: {'dev': 39, 'valid': 22, 'test': 35}

| 方案 | Acc | Prec | Recall | F1 | FPR | FNR | 事件召回 | 提前≥30s覆盖 | 事件精确率 | 误报/h | 中位提前s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 原始GRU | 0.9327 | 0.4318 | 0.3662 | 0.3963 | 0.0309 | 0.6338 | 0.9143 | 0.8286 | 0.4717 | 12.4444 | 76.5 |
| 校准+稳定化 | 0.9004 | 0.3131 | 0.5448 | 0.3977 | 0.0767 | 0.4552 | 0.9714 | 0.9143 | 0.4037 | 7.2222 | 87.0 |
| 分级融合(B4∪GRU) | 0.8811 | 0.2884 | 0.6614 | 0.4016 | 0.1048 | 0.3386 | 1.0 | 0.9429 | 0.227 | 65.4444 | 113.0 |

- 稳定化参数: `{'high': 0.1661, 'low': 0.083, 'confirm_s': 0.0, 'cooldown_s': 60.0}`
- 融合 B4 带宽: 0.0 m3/min

## 分场景 (优化后)

- **normal**: events=0 detected=0 lead≥30=None median=Nones
- **low_stable**: events=0 detected=0 lead≥30=None median=Nones
- **critical**: events=7 detected=7 lead≥30=1.0 median=97.0s
- **high_risk**: events=14 detected=14 lead≥30=0.9286 median=77.5s
- **rapid**: events=2 detected=1 lead≥30=0.0 median=24.0s
- **noisy**: events=12 detected=12 lead≥30=1.0 median=92.5s