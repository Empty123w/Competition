"""控制安全联锁 (阶段 C): 任何指令下发前必须通过的硬约束。"""
from __future__ import annotations


def check_pressure_setpoint(setpoint: float, cfg: dict) -> tuple[bool, str]:
    lo, hi = cfg.get("safety_interlock", {}).get("require_pressure_within", [0.55, 0.75])
    if not (float(lo) <= float(setpoint) <= float(hi)):
        return False, f"压力设定 {setpoint} MPa 超出安全区间 [{lo}, {hi}] MPa"
    return True, "ok"


def check_command(device_id: str, action_type: str, target_flow_m3_min: float | None,
                  rated_flow_m3_min: float, sim_snapshot: dict, cfg: dict) -> tuple[bool, str]:
    """综合校验一条针对某设备的控制指令。"""
    inter = cfg.get("safety_interlock", {})
    if device_id not in {"4#", "5#"}:
        return False, f"未知设备: {device_id}"
    if action_type not in ("run", "stop", "set_load"):
        return False, f"不支持的动作: {action_type}"

    mine = next((d for d in sim_snapshot.get("devices", [])
                 if d["device_id"] == device_id), None)
    running_before = bool(mine and mine.get("running"))

    # 至少保留 N 台运行
    min_running = int(inter.get("min_running_units", 1))
    running_now = sum(1 for d in sim_snapshot.get("devices", []) if d.get("running"))
    will_stop = action_type == "stop" or (
        action_type == "set_load" and not target_flow_m3_min)
    if will_stop and running_before and (running_now - 1) < min_running:
        return False, f"安全联锁: 操作后运行机组数 {running_now - 1} 低于最小要求 {min_running}"

    if action_type in ("run", "set_load"):
        if target_flow_m3_min is None:
            return False, "运行/加载指令必须提供 target_flow_m3_min"
        flow = float(target_flow_m3_min)
        if flow < 0 or flow > float(rated_flow_m3_min) + 1e-6:
            return False, f"目标流量 {flow} m3/min 超出 [0, {rated_flow_m3_min}] m3/min"
    return True, "ok"
