# 最终版本与交付核查

## 1. 团队 main 实际采用的算法 (08915e4 (Wxyun113/Competition main))

- 调度: B `optimization/b_api.plan_dispatch` (b3/balanced/load_balanced); C 通过 `integration/dispatch_bridge.py` 调用。
- 安全: B `safety/surge.assess_surge_risk` (代理)。
- A: 预测/健康/RUL/维护服务; K: 知识库; C: FastAPI + MCP(10) + agent; D: 无。

## 2. 尚未集成到 main 的实验结果 (B 分支)

| 项 | 状态 | 说明 |
|---|---|---|
| joint / joint_nompc 主调度 | 未接入 B6 | 仅内部候选 (`stage2/joint_dispatch.py`), `simulation_candidate_only` |
| MPC 压力控制 / ML 压力模型 | 未接入 | ML 压力模型零增量, 可不部署 |
| GRU 喘振预警 | 未接入 | 独立候选; B4 仍为在线阻断 |
| DQN/RL | 未接入 | 未超越 MPC, 不部署 |

## 3. 关键接口接入状态

| 问题 | 结论 |
|---|---|
| joint_nompc 是否已接入 B6 | **否** (B6 `plan_dispatch` 仍为 b3/balanced/load_balanced) |
| C 是否支持新模式 | C 走 `dispatch_bridge` 传 mode; 需扩 mode 才能用 joint |
| D 引用旧指标 | D 未实现, 无引用 |

## 4. 建议保留模块

| 模块 | 保留 | 理由 |
|---|---|---|
| B3 / balanced / LRB | ✅ | 现网稳定, C 已集成 |
| joint_nompc (调度) | ✅ 候选 | pooled SEC 7.84%, 低负荷 69.8%, 偏离最大 9.999pp |
| MPC (压力) | ✅ 场景层 | step/peak/fault 鲁棒; 真实平滑窗增量≈0 |
| ML 压力模型 | ❌ | 相对简单模型零增量 |
| GRU 预警 | ✅ 独立模块 | 提前≥30s 覆盖 0.49→0.91 |
| DQN/RL | ❌ | 未超越 MPC |

## 5. 正式推送前剩余阻断项

1. D 前端可视化与可解释展示 (赛题必交, 当前缺失)。
2. C 自然语言理解 ≥90% 的验收测试集与准确率证据。
3. 若采用 joint_nompc: 扩 B6/C mode 并完成 C 端到端时延验证。
4. 交付物: 方案书 / 技术总报告 / 演示视频 / 路演 / 知识产权。
5. 现场类指标 (真实停机/喘振/压力) 需现场数据复评, 现阶段仅仿真自证。
