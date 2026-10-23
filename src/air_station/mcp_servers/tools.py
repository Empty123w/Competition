"""MCP 工具实现 (阶段 C)。

5 个必备 MCP 服务(赛题要求) + 辅助工具:
  1. forecast_load        负荷预测 (A)
  2. dispatch_optimize    调度优化 (调用 B 的 plan_dispatch)
  3. health_diagnose      健康诊断 (A)
  4. rul_estimation       剩余寿命估计 (A, 模拟)
  5. surge_warning        喘振预警 (调用 B 的 assess_surge_risk)
  6. control_command      控制指令(SCADA/PLC/EAM/工单)
  7. orchestrate          自然语言编排(Agent 工作流)

调度与喘振直接调用 B 的稳定接口, C 只做输入组装与输出透传,
不复制 B 的算法, 不修改 B 的成功字段。
"""
from __future__ import annotations


def forecast_load(horizons: list[int] | None = None,
                  processed_dir: str = "data/processed",
                  config_path: str = "configs/forecasting.yaml",
                  model_dir: str = "artifacts/models") -> dict:
    """预测未来 15/30/60 分钟用气负荷(点预测 + 区间)。"""
    from air_station.models.forecasting.service import forecast_json
    return forecast_json(processed_dir=processed_dir, config_path=config_path,
                         model_dir=model_dir, horizons=horizons)


def health_diagnose(processed_dir: str = "data/processed",
                    thresholds_path: str = "configs/health_thresholds.yaml") -> dict:
    """对 4#/5# 机组输出健康评分、风险等级与检修建议(无监督)。"""
    from air_station.models.health.service import health_json
    return health_json(processed_dir=processed_dir, thresholds_path=thresholds_path)


def rul_estimation(config_path: str = "configs/rul.yaml",
                   sequences_path: str = "data/simulated/rul_sequences.parquet",
                   model_dir: str = "artifacts/models") -> dict:
    """设备剩余寿命估计(模拟退化数据训练, validation_mode=simulation)。"""
    from air_station.models.rul.service import rul_json
    return rul_json(config_path=config_path, sequences_path=sequences_path,
                    model_dir=model_dir)


def dispatch_optimize(mode: str = "b3",
                      demand_flow_m3_min: float | None = None,
                      horizon_minutes: int = 15,
                      timeout_ms: float | None = None,
                      optimization_config: str = "configs/optimization.yaml") -> dict:
    """多机协同调度优化(B 的 plan_dispatch)。

    mode ∈ {b3 能效优先, balanced 低负荷优先, load_balanced 加载率均衡};
    输出 simulation_candidate_only, 不含任何 PLC 执行许可。
    """
    from air_station.integration import dispatch_plan
    return dispatch_plan(mode=mode, demand_flow_m3_min=demand_flow_m3_min,
                         horizon_minutes=horizon_minutes, timeout_ms=timeout_ms,
                         optimization_config=optimization_config)


def surge_warning(config_path: str = "configs/safety.yaml",
                  boundary_path: str = "artifacts/models/surge_boundary.json") -> dict:
    """离心机喘振风险代理评估(B 的 assess_surge_risk, 非真实喘振线)。"""
    from air_station.integration import surge_assessment
    return surge_assessment(config_path=config_path, boundary_path=boundary_path)


def control_command(action: str,
                    device_id: str | None = None,
                    action_type: str | None = None,
                    target_flow_m3_min: float | None = None,
                    plan: dict | None = None,
                    reason: str | None = None,
                    priority: str = "中",
                    minutes: int = 60,
                    persist: bool = True,
                    config_path: str = "configs/control.yaml") -> dict:
    """控制指令服务。action ∈ {scada_query, scada_history, plc_command,
    execute_plan, eam_maintenance, work_order, command_status}。

    仅作用于模拟 PLC/SCADA/EAM; 下发前经过安全联锁与 B 结果准入闸门。
    """
    from air_station.control.service import control_json
    payload: dict = {}
    if device_id is not None:
        payload["device_id"] = device_id
    if action_type is not None:
        payload["action_type"] = action_type
    if target_flow_m3_min is not None:
        payload["target_flow_m3_min"] = target_flow_m3_min
    if plan is not None:
        payload["plan"] = plan
    if reason is not None:
        payload["reason"] = reason
    if priority is not None:
        payload["priority"] = priority
    if minutes is not None:
        payload["minutes"] = minutes
    return control_json(action, payload, persist=persist, config_path=config_path)


def create_work_order(device_id: str, reason: str | None = None,
                      priority: str = "中", persist: bool = True) -> dict:
    """生成设备维保工单(模拟 EAM)。"""
    return control_command("work_order", device_id=device_id, reason=reason,
                           priority=priority, persist=persist)


def orchestrate(user_input: str, execute: bool = False,
                persist: bool = True,
                config_path: str = "configs/agent.yaml") -> dict:
    """自然语言编排: 意图理解 -> 工具链调用 -> 方案与可解释链路。"""
    from air_station.agent.workflow import run_agent
    return run_agent(user_input, execute=execute, persist=persist,
                     config_path=config_path)


TOOL_SPECS = [
    {"name": "forecast_load", "fn": forecast_load},
    {"name": "health_diagnose", "fn": health_diagnose},
    {"name": "rul_estimation", "fn": rul_estimation},
    {"name": "dispatch_optimize", "fn": dispatch_optimize},
    {"name": "surge_warning", "fn": surge_warning},
    {"name": "control_command", "fn": control_command},
    {"name": "create_work_order", "fn": create_work_order},
    {"name": "orchestrate", "fn": orchestrate},
]
