"""模拟执行环境: 站级状态机 + 模拟 SCADA/PLC 反馈 (阶段 C)。

赛题明确 PLC/SCADA 为模拟接口, 本模块仅在内存中维护站级状态,
绝不连接真实设备。所有下发指令先经控制服务的安全联锁校验。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_STATE: dict | None = None
_LOCK = threading.Lock()


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _estimate_power(device_id: str, flow: float) -> float:
    """用 B 的功率模型估算执行后的功率(只读, 不写真实设备)。"""
    try:
        from air_station.optimization.performance import estimate_power
        r = estimate_power(device_id, flow)
        if r.get("status") == "success":
            return float(r["estimated_power_kw"])
    except Exception:  # noqa: BLE001  模型不可用时不阻断模拟反馈
        pass
    return 0.0


def _load_initial_states(processed_dir: str) -> dict:
    import pandas as pd
    src = Path(processed_dir) / "device_timeseries.parquet"
    if not src.exists():
        raise FileNotFoundError(f"[control] 运行数据缺失, 无法初始化模拟站: {src}")
    cols = ["time", "device_id", "running", "flow_m3_min", "discharge_pressure_mpa",
            "igv_percent", "power_kw", "load_ratio", "maintenance_remaining_hours"]
    df = pd.read_parquet(src, columns=cols)
    devices = {}
    for dev_raw, g in df.sort_values("time").groupby("device_id", observed=True):
        last = g.iloc[-1]
        dev = f"{int(dev_raw)}#"
        running = bool(last["running"])
        flow = float(last["flow_m3_min"]) if last["flow_m3_min"] == last["flow_m3_min"] else 0.0
        devices[dev] = {
            "device_id": dev,
            "running": running,
            "action": "run" if running else "stop",
            "flow_m3_min": flow if running else 0.0,
            "power_kw": float(last["power_kw"]) if last["power_kw"] == last["power_kw"] else 0.0,
            "igv_percent": float(last["igv_percent"]) if last["igv_percent"] == last["igv_percent"] else 0.0,
            "discharge_pressure_mpa": float(last["discharge_pressure_mpa"])
            if last["discharge_pressure_mpa"] == last["discharge_pressure_mpa"] else None,
            "maintenance_remaining_hours": float(last["maintenance_remaining_hours"])
            if last["maintenance_remaining_hours"] == last["maintenance_remaining_hours"] else None,
            "connected": True,
        }
    return devices


def reset(processed_dir: str = "data/processed") -> dict:
    global _STATE
    with _LOCK:
        _STATE = {"created_at": _utc_iso(), "devices": _load_initial_states(processed_dir)}
    return _STATE


def _ensure(processed_dir: str) -> dict:
    global _STATE
    if _STATE is None:
        reset(processed_dir)
    return _STATE


def snapshot(processed_dir: str = "data/processed") -> dict:
    state = _ensure(processed_dir)
    with _LOCK:
        created = state["created_at"]
        devices = [dict(d) for d in state["devices"].values()]
    devices.sort(key=lambda d: d["device_id"])
    return {"status": "success", "source": "simulated_scada",
            "snapshot_at": _utc_iso(), "initialized_at": created,
            "devices": devices}


def apply_command(device_id: str, action_type: str, target_flow_m3_min: float | None,
                  rated_flow_m3_min: float, processed_dir: str = "data/processed",
                  latency_ms: int = 80, feedback_delay_ms: int = 120) -> dict:
    """把一条控制指令应用到模拟站, 返回执行反馈。

    action_type ∈ {run, stop, set_load}。
    """
    state = _ensure(processed_dir)
    if latency_ms:
        time.sleep(latency_ms / 1000.0)
    with _LOCK:
        if device_id not in state["devices"]:
            return {"success": False, "message": f"未知设备: {device_id}"}
        dev = state["devices"][device_id]
        rated = float(rated_flow_m3_min)

        if action_type == "stop":
            dev.update(running=False, action="stop", flow_m3_min=0.0,
                       power_kw=0.0, igv_percent=0.0)
        elif action_type == "run":
            flow = float(target_flow_m3_min) if target_flow_m3_min else rated * 0.8
            flow = max(0.0, min(flow, rated))
            dev.update(running=True, action="run", flow_m3_min=flow,
                       power_kw=_estimate_power(device_id, flow),
                       igv_percent=min(flow / rated * 100.0, 100.0))
        elif action_type == "set_load":
            if not dev["running"]:
                dev.update(running=True)
            flow = float(target_flow_m3_min) if target_flow_m3_min else rated * 0.8
            flow = max(0.0, min(flow, rated))
            dev.update(action="run", flow_m3_min=flow,
                       power_kw=_estimate_power(device_id, flow),
                       igv_percent=min(flow / rated * 100.0, 100.0))
        else:
            return {"success": False, "message": f"不支持的动作: {action_type}"}
        snapshot_dev = dict(dev)

    if feedback_delay_ms:
        time.sleep(feedback_delay_ms / 1000.0)
    return {
        "success": True,
        "message": "指令已由模拟 PLC 执行并反馈",
        "device_id": device_id,
        "action_type": action_type,
        "actual_flow_m3_min": round(float(snapshot_dev["flow_m3_min"]), 2),
        "actual_power_kw": round(float(snapshot_dev["power_kw"]), 1),
        "igv_percent": round(float(snapshot_dev["igv_percent"]), 1),
        "running": bool(snapshot_dev["running"]),
        "feedback_at": _utc_iso(),
        "source": "simulated_plc",
    }
