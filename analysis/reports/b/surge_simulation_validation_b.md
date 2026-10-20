# B4 喘振预警的独立高频(秒级)仿真验证

- 负责人：成员 B ｜ 分支：`feat/b-optimization-aligned`（本地，未提交/未推送）
- 日期：2026-10-18 ｜ 脚本：`analysis/scripts/surge_simulation_validation.py`
- 产物：`analysis/reports/b/surge_simulation_validation.json`
- 赛题指标：**喘振预警准确率 ≥90%**、**预警提前时间 ≥30 s**
- 性质：**合成秒级仿真**；非现场结果，非分钟插值

---

## 1. 预先固定的定义（防自测达标）

| 项 | 定义 |
|---|---|
| 事件(真值) | 合成物理流量 `< q_true(p)` 连续 ≥3 s；事件时刻=首个满足秒 |
| **真值线** | 独立参数 `q_true = 80 + 260·(p−0.66)`，**与 B4 配置边界(90/350)不同** |
| 报警 | B4 `risk_level ∈ {medium, high}`；`unknown` **单列**，不计报警 |
| 提前时间 | `事件时刻 − 首次报警时刻`（s） |
| 指标 | 事件级 精确率/召回/F1/FPR/FNR、逐事件 lead、lead≥30s 覆盖、unknown 秒数 |

- **复用冻结 B4 代理，未改其阈值/边界**；真值线独立，故为“B4 被测试”而非“自测”。

## 2. 场景划分

- **dev**：`normal_steady`、`normal_small`、`dev_gradual`（用于选定早预警带）
- **valid**：`gradual_approach`、`rapid_load_change`、`machine_switch`、`noisy`、`sensor_missing`

## 3. 冻结 B4 结果（valid）

| 场景 | 事件数 | 检出 | 逐事件 lead(s) | P | R | F1 | FPR | FNR | unknown(s) |
|---|---|---|---|---|---|---|---|---|---|
| gradual_approach | 2 | 2 | 0, 4 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0 |
| noisy | 2 | 2 | 21, 29 | 0.998 | 1.0 | 0.999 | 0.005 | 0.0 | 0 |
| sensor_missing | 1 | 1 | 7 | 0.992 | 0.816 | 0.895 | 0.018 | 0.184 | 79 |
| rapid_load_change / machine_switch | 0 | — | — | — | — | — | 0.0 | — | 0 |

- **汇总**：mean precision 0.996、mean recall 0.939、**lead≥30s 覆盖率 = 0.0**。
- 冻结 B4 能检出事件，但**提前时间不足 30 s**（0–29 s），且传感缺失时召回下降、产生 unknown。

## 4. 独立早预警层（新算法，独立保存，不改 B4）

- 定义：`alarm = flow < q_b4(p) + band`（band 为工程裕度带）。
- **在 dev 场景选定并冻结 band**（0.05 FPR 上限、要求 dev 最小 lead≥30s）：

| band(m³/min) | dev 最小 lead(s) | dev FPR |
|---|---|---|
| 20 | 18 | 0.0094 |
| 30 | 27 | 0.0144 |
| **40（选定）** | **35** | **0.0189** |

- **valid 结果**（band=40）：5 事件全检出，**lead≥30s 覆盖率 = 1.0**；各场景 lead：gradual 44/48、noisy 84/92、sensor_missing 61。

## 5. 结论（如实）

1. **冻结 B4**：事件级检出尚可，但**不满足 lead≥30s**（覆盖率 0）。
2. **独立早预警层（band=40）**：在合成场景达到 **lead≥30s 覆盖 1.0**、dev FPR≈1.9%；属**新算法**，需现场高频数据再验证。
3. 全部为**合成秒级仿真**，**不得当作现场准确率/提前时间**；传感缺失场景暴露 B4 的 unknown 处理。
4. `rapid_load_change`/`machine_switch` 在本次合成参数下**未触发真值事件**，仅验证无虚报（FPR 0）。

## 6. 缺口

| 缺口 | 用途 |
|---|---|
| 现场**秒级**压力/流量/振动/IGV/BOV | 真实提前时间与误报率 |
| 厂家喘振边界或真实喘振正例 | 替换合成真值线 |
| 喘振前兆事件标注 | 事件级真值 |

## 7. 复现

```bash
python3 analysis/scripts/surge_simulation_validation.py
```
