"""B 组第二阶段: 面向比赛指标的自主优化与仿真验证 (子包)。

模块:
  - protocol      : 冻结的评价协议与指标定义 (实验前固定, 不事后修改)
  - surge_sim     : 独立高频喘振工程仿真 (事件生成机制与被评估预警器独立)
  - surge_warn    : 喘振预警模型 (LightGBM / GRU / LSTM / 集成 + B4 代理融合)
  - pressure_model: 母管压力动态 ML 模型 (含 B2 物理模型残差学习)
  - mpc_controller: 多时段滚动 MPC (压力感知; ML 压力模型)
  - rl_env/rl_agent: 安全约束强化学习 (环境 + 策略, 多随机种子)

约束: 全部 validation_mode=simulation; 不修改冻结 B1-B4 与 B6 公共契约;
      保持 simulation_candidate_only; 禁止真实设备控制。
"""
from __future__ import annotations

__all__ = [
    "protocol",
    "surge_sim",
    "surge_warn",
    "pressure_model",
    "mpc_controller",
    "rl_env",
    "rl_agent",
]
