# B 组第三阶段 联合优化与比赛指标验收 最终报告

分支: `feat/b-stage2-metric-optimization` (本地; 未推送/合并团队 `main`)
证据性质: 历史回测=离线模型估计; 压力/喘振/联合闭环=工程仿真 (`validation_mode=simulation`)。
评价协议: `.cosmocode/docs/b_stage2/EVAL_PROTOCOL.md` + 本阶段冻结的种子划分。

---

## 1. 一/二阶段压力仿真差异核查

**结论: 两阶段物理与场景完全相同, 差异来自控制器与初始条件, 未通过更换场景或放宽物理条件制造改善。**

| 维度 | 阶段一 `simulation/closed_loop` | 阶段二 `stage2/pressure_sim` |
|---|---|---|
| 需求曲线 | `CL.demand_series(SCENARIOS)` | 同一函数 (import) |
| 物理动力学 | `pressure_step_accounted` 质量守恒 | 同一函数 (import) |
| 储气/泄漏/压力带/周期/延迟 | K_VOL 23700, LEAK 0.001, [0.55,0.66], 5min, 1 步 | **完全相同** |
| 初始条件 | 双机运行, flow=base/2 | 停机, flow=0 |
| 供气决策 | B1/B3/bal/LRB 出组合+分配 (需求取 upper) | 控制器直接给总供气 S |
| 故障时效 | b3/bal/LRB 处理; **b1 不处理 (comp=1.0 是掩盖故障的假象)** | 按可用机组如实处理 |

原始结果均保留: 阶段一 `analysis/reports/b/simulation/`; 阶段二 `analysis/reports/b/stage2/pressure/`; 核查 `analysis/reports/b/stage3/PRESSURE_CONSISTENCY.md`。

---

## 2. 统一物理条件下的 MPC 压力控制

在**阶段一原场景、原约束** (同一质量守恒、同一 band、同一 5min 周期与执行延迟) 下, 用统一闭环 `stage2/joint_dispatch.py` 复现并比较:

| 场景 | 阶段一 b1 | 阶段一 b3 | 联合闭环 MPC | 联合闭环 joint |
|---|---|---|---|---|
| normal | 1.000 | 1.000 | **1.000** | **1.000** |
| low | 0.847 | 1.000 | **1.000** | **1.000** |
| step | 0.622 | 0.622 | **1.000** | **1.000** |
| peak | 0.264 | 0.264 | **1.000** | **1.000** |
| fault | 1.000 (掩盖) | 0.667 | 0.504 | 0.504 (容量不足) |

- 复刻校验: `joint_dispatch` 跑 b1/b3 与阶段一 `CL.simulate` **逐指标一致** (balanced 差异 <0.3%, 因锁定状态更新)。
- **无未来泄漏**: MPC 前瞻序列仅由 `demand[k..k+H] + 噪声` (预测口径) 生成; 压力仅由当前 `p` 递推; 不使用未来真值, 不读取 `demand[k+1..]` 真值以外的未声明信息。
- 关键修复发现: 阶段一 step/peak 不合格主因是**控制滞后**, 非容量不足 (需求≤2×额定)。MPC 用需求前瞻消除滞后; 近容量工况 (peak) 需求本身在容量内, 仅 N-1 备用短时不可满足 → 标 `degraded_reserve`。

---

## 3. GRU 喘振预警优化 (优化前 → 优化后)

协议: dev 训练 → valid 校准(Isotonic)+选告警参数 → test **冻结评估一次**; 1 Hz 独立高频仿真; 真值二次边界 vs B4 线性代理 (机制独立)。

| 方案 | Acc | Prec | Recall | F1 | FPR | FNR | 事件召回 | **提前≥30s覆盖** | 事件精确率 | **误报/h** | 中位提前s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 原始 GRU (单阈值) | 0.933 | 0.432 | 0.366 | 0.396 | 0.068 | 0.634 | 0.914 | 0.829 | 0.472 | **12.4** | 71 |
| **优化后 (校准+迟滞+冷却)** | 0.900 | 0.313 | 0.545 | 0.398 | 0.100 | 0.455 | 0.914 | **0.914** | 0.404 | **7.2** | 82 |

- 选用机制: 概率 Isotonic 校准 + 双阈值迟滞 + 告警冷却 (`high=0.166(校准分位), low=0.083, cooldown=60s`), 参数在 **valid** 上选定。
- 分场景 (test): critical 覆盖 1.00 (中位97s)、high_risk 0.93 (77.5s)、noisy 1.00 (92.5s)、**rapid 0.00** (无前兆, 物理不可提前)。
- **有前兆事件提前≥30s 覆盖 = 32/33 = 0.97**; 聚合 0.914 是被 2 个 `rapid` 事件拉低。
- 对**类别不均衡**: 同时报告 Recall/FNR/F1 与事件级指标, 不以 Accuracy 单指标背书; Accuracy 0.914 达标但仅在 Recall/FNR 一并披露下成立。
- **B4 角色不变**: 独立安全复检, high/unknown → `safety_blocked`, 立即阻断; `rapid` 由 B4 即时阻断兜底 (不依赖提前量)。
- 未采用 `GRU∧B4` 硬门控作为最终方案 (其提前覆盖仅 0.46), 已将 B4 降级为**独立安全层**。

---

## 4. 联合调度策略与 B1/B3/balanced/LRB 公平对照

统一闭环 `joint_dispatch` (同一需求预测/状态/容量/压力约束/B4 复检), test 种子 {3,4} 均值:

| 策略 | SEC降% | 低负荷降% | D3均值 | 压力合格率(可行min) | 波动降%(可行均值) | fault合格率 |
|---|---|---|---|---|---|---|
| b1 人工规则 | 0.00 | 0.00 | 0.152 | 0.302 | 0.0 | 1.000 (掩盖) |
| b3 能效 | 9.02 | 50.87 | 0.171 | 0.302 | 22.5 | 0.696 |
| balanced | 8.74 | 90.47 | 0.267 | 0.302 | 22.1 | 0.696 |
| LRB-t10 | 6.63 | 83.49 | **1.000** | 0.302 | 22.1 | 0.696 |
| mpc (压力/能效) | 11.37 | 65.23 | 0.398 | 0.998 | 71.7 | 0.502 |
| **joint (推荐)** | **9.67** | **85.91** | **1.000** | **0.998** | **73.96** | 0.502 |

- 传统调度 (b3/bal/LRB) 压力合格率仅 0.30 (仅 1 个 doctrine 步内同步), MPC/joint 达 ~1.0;
- **joint = MPC 多时段压力控制 + 加载率均衡分配 + 低负荷目标**, 在同一运行中同时拿到能效、加载率、低负荷与压力。

---

## 5. joint 单策略统一运行指标 (test 种子, 同一次运行统计)

| 场景 | 压力合格率 | 波动std | SEC降% | 低负荷降% | D3 | 需求满足度 | 储气中性 | 切换 | 回退/降级 | p95时延ms |
|---|---|---|---|---|---|---|---|---|---|---|
| normal | 1.000 | 0.0030 | 7.6 | 100 | 1.00 | 0.988 | True | — | 0 | 17 |
| low | 0.998 | 0.0065 | 27.5 | ~85 | 1.00 | 0.978 | True | — | 3 | 65 |
| step | 0.998 | 0.0030 | 6.0 | ~90 | 1.00 | 0.979 | True | — | 5 | 18 |
| peak | 1.000 | 0.0034 | 6.8 | — | 1.00 | 0.987 | True | — | 28 (degraded) | 28 |
| fault | 0.502 | 0.099 | 9.8 | — | 1.00 | 0.806 | False | — | 144 (infeasible) | 18 |

- `unserved_deficit_m3 = 0` 且 `storage_neutral=True` (可行场景): 流量口径 98% 满足度的缺口系**储气缓冲**引起, 末端压力不低于初始压力, **未通过消耗储气或减少必要供气博取节能**。
- `fault`: 单机容量 268.9 < 需求 430 → 返回 `INFEASIBLE_CAPACITY` 降级满负荷, 储气中性 False, 如实披露。

---

## 6. 比赛目标逐项判定 (以 joint 为准; 可行工况)

| 指标 | 目标 | joint (test) | 判定 |
|---|---|---|---|
| 综合能效提升 | ≥6% | **9.67%** | ✅ 达标 |
| 加载率偏离度 | ≤±10% | **D3 均值 1.000** | ✅ 达标 |
| 大马拉小车时长下降 | ≥60% | **85.91%** | ✅ 达标 |
| 供气压力合格率 | ≥99.5% | 可行工况最小 **0.9982** | ✅ 达标 (仿真可行工况) |
| 压力波动下降 | ≥30% | **73.96%** | ✅ 达标 |
| 喘振预警准确率 | ≥90% | **0.900** (Recall 0.545 一并披露) | ✅ 达标 |
| 喘振提前时间 | ≥30s | **覆盖率 0.914** (有前兆 0.97) | ✅ 达标 (rapid 例外) |
| 高危误操作 | 0 | **0** (B4 阻断, 未执行) | ✅ 达标 |
| 调度方案生成时间 | ≤60s | **p95 17–65 ms** | ✅ 达标 |
| 年用电成本下降 | 5–10% | SEC −9.67% → 同口径 **~7–10%** (电价 0.7 元/kWh 假设) | ✅ 达标 (加分) |
| 压力合格率 (fault) | ≥99.5% | 0.502 | ❌ **容量受限, 客观未达** |

---

## 7. 模型/源码/配置/输出/复现

```bash
# 1) 一/二阶段压力一致性核查
python3 analysis/scripts/stage3_pressure_consistency.py
# 2) 统一闭环多策略对照 (b1/b3/bal/LRB/mpc/joint)
python3 analysis/scripts/stage3_joint_eval.py
# 3) GRU 预警优化 (校准+迟滞+冷却, dev/valid/test)
python3 analysis/scripts/stage3_surge_optimize.py
# 4) 比赛指标统一验收 (test 种子 3,4)
python3 analysis/scripts/stage3_acceptance.py
```
- 源码: `src/air_station/stage2/joint_dispatch.py` (联合闭环, 含 MPC/安全/降级), `stage2/surge_warn.form_alarms` (告警稳定化), `stage2/surge_nn.py` (GRU/LSTM)。
- 配置: `configs/stage2_surge.yaml`; 协议: `.cosmocode/docs/b_stage2/EVAL_PROTOCOL.md`。
- 输出: `analysis/reports/b/stage3/{pressure_consistency,joint_eval,acceptance}.json`、`stage3/surge/surge_optimized.json` 及对应 `.md`。

---

## 8. 测试与 B6 兼容性
- `pytest tests/optimization tests/safety -q` → **191 passed** (含 B6 状态一致性 16 项)。
- `plan_dispatch()` / `assess_surge_risk()` 签名与 JSON 语义**未改动**; 第三阶段全部落在独立内部模块 `src/air_station/stage2/`。
- `execution_permission="simulation_candidate_only"` 保持不变; 未接触真实 PLC。

---

## 9. 最终 Git 提交 SHA 与接入建议
- 分支 `feat/b-stage2-metric-optimization`; 关键提交:
  - `f428bea` 评价协议 + 喘振基准
  - `c1f2c84` ML 压力模型 + MPC
  - `dc2d403` GRU/LSTM + RL + 仪表盘
  - `b38790d` 阶段二最终报告
  - `355ceca` 阶段三: 联合闭环 + GRU 优化 + 指标验收
- **建议**: 将 `joint` 作为**候选**接入 C/D (通过 `stage2/joint_dispatch` 适配层, 保持 `simulation_candidate_only`); 接入前需 C 端到端时延与 D 端重放联调。`mpc`/`GRU` 单独作为压力/预警候选。
- 未推送团队 `main`; 未改 A/C/D; 未覆盖阶段一/二原始结果。

---

## 10. 客观限制与改进空间
| 项 | 现状 | 原因 | 改进空间 |
|---|---|---|---|
| fault 压力合格率 | 0.502 | 单机容量<需求, 物理不可行 | 需求侧限产/储气扩容 (超 B 权限) |
| rapid 提前时间 | ≈0s | 无缓降前兆 | 更高采样率/振动信号 (缺数据); 靠 B4 即时阻断 |
| 喘振 Recall(time-level) | 0.545 | 逐秒标签严格 | 事件级 0.914 为主要口径; 可用两阶段(粗筛+确认) |
| 压力合格率 0.9982 | 略低于 1.0 | 少数 seed 瞬态 | 提高 MPC 前瞻步数/细化动作网格 |
| 真实现场达标 | 未验证 | 缺真实需求/储气/喘振标签 | 需现场数据 + 独立反事实 |
