"""控制指令服务 (阶段 C): 模拟 PLC/SCADA/EAM 与执行闭环。

唯一对外入口: control_json()
支持 action:
  - "scada_query"      站级实时快照(模拟 SCADA)
  - "scada_history"    单机历史序列
  - "plc_command"      单条控制指令下发(带安全联锁)
  - "execute_plan"     执行 B 的调度方案(准入闸门 + 闭环: 下发->反馈)
  - "eam_maintenance"  维保计划查询(模拟 EAM)
  - "work_order"       工单生成(模拟 EAM)
  - "command_status"   查询指令执行状态

准入闸门: B 输出恒为 simulation_candidate_only; 仅当 `candidate_usable` 为真
(求解成功且未被安全阻断) 才允许在模拟环境执行; 回退/不可行/阻断一律拒绝并需人工复核。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from air_station.control import eam, interlock, simulator
from air_station.db import repository

DEFAULT_CONFIG = "configs/control.yaml"
DEFAULT_PROCESSED = "data/processed"
DEFAULT_ARCHIVE = "data/设备档案.json"


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    import yaml
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[control] 配置文件缺失: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _persist_enabled(persist: bool) -> bool:
    if not persist:
        return False
    try:
        repository.ensure_db()
        return True
    except Exception:  # noqa: BLE001
        return False


def _rated_flow(cfg: dict) -> float:
    """额定流量取 B 的运行约束(阈值外部化)。"""
    try:
        from air_station.integration import build_constraints
        return float(build_constraints(
            cfg.get("paths", {}).get("optimization_config", "configs/optimization.yaml")
        ).rated_capacity_m3min)
    except Exception:  # noqa: BLE001
        return 268.9


def _plan_gate(plan: dict) -> tuple[bool, str]:
    """B 结果准入闸门: 仅 candidate_usable(成功且未阻断) 才允许模拟执行。"""
    from air_station.optimization import b_api
    if b_api.is_blocked(plan):
        return False, "B 安全阻断(safety_blocked=true): 禁止执行, 需人工复核"
    if b_api.is_infeasible(plan):
        return False, "容量/状态不可行: 无安全自动方案, 需人工复核"
    if b_api.is_fallback_result(plan):
        return False, "B 结果为失败回退(is_fallback=true): 非有效优化方案, 需人工复核"
    if not b_api.is_success(plan):
        return False, f"B 状态非成功: {plan.get('status')}"
    return True, "ok"


def scada_query(cfg: dict, device_id: str | None = None) -> dict:
    processed = cfg.get("paths", {}).get("processed_dir", DEFAULT_PROCESSED)
    snap = simulator.snapshot(processed)
    if device_id:
        snap["devices"] = [d for d in snap["devices"] if d["device_id"] == device_id]
    snap["stale_seconds"] = cfg.get("simulation", {}).get("scada_stale_seconds", 60)
    return snap


def scada_history(cfg: dict, device_id: str, minutes: int = 60) -> dict:
    import pandas as pd
    processed = cfg.get("paths", {}).get("processed_dir", DEFAULT_PROCESSED)
    src = Path(processed) / "device_timeseries.parquet"
    if not src.exists():
        raise FileNotFoundError(f"[control] 运行数据缺失: {src}")
    cols = ["time", "device_id", "flow_m3_min", "discharge_pressure_mpa",
            "power_kw", "load_ratio", "running"]
    df = pd.read_parquet(src, columns=cols)
    df = df[df["device_id"].astype(str).str.startswith(device_id.rstrip("#"))]
    df = df.sort_values("time").tail(int(minutes))
    records = []
    for _, r in df.iterrows():
        records.append({"time": pd.Timestamp(r["time"]).isoformat(),
                        "flow_m3_min": None if pd.isna(r["flow_m3_min"]) else round(float(r["flow_m3_min"]), 2),
                        "discharge_pressure_mpa": None if pd.isna(r["discharge_pressure_mpa"])
                        else round(float(r["discharge_pressure_mpa"]), 3),
                        "power_kw": None if pd.isna(r["power_kw"]) else round(float(r["power_kw"]), 1),
                        "load_ratio": None if pd.isna(r["load_ratio"]) else round(float(r["load_ratio"]), 3),
                        "running": bool(r["running"])})
    return {"status": "success", "source": "simulated_scada", "device_id": device_id,
            "minutes": int(minutes), "records": records}


def plc_command(cfg: dict, device_id: str, action_type: str,
                target_flow_m3_min: float | None = None, plan_id: str | None = None,
                persist: bool = True) -> dict:
    t0 = time.perf_counter()
    rated = _rated_flow(cfg)
    processed = cfg.get("paths", {}).get("processed_dir", DEFAULT_PROCESSED)

    snap = simulator.snapshot(processed)
    ok, reason = interlock.check_command(device_id, action_type, target_flow_m3_min,
                                         rated, snap, cfg)
    use_db = _persist_enabled(persist)
    command_id = None
    if use_db:
        command_id = repository.create_control_command(
            device_id, action_type, float(target_flow_m3_min or 0.0),
            float(target_flow_m3_min or 0.0) / max(rated, 1e-6),
            plan_id=plan_id)

    if not ok:
        if use_db and command_id:
            repository.mark_command(command_id, "rejected", reason)
        return {"status": "success", "accepted": False, "command_id": command_id,
                "rejected_reason": reason, "device_id": device_id,
                "action_type": action_type, "source": "simulated_plc"}

    sim_cfg = cfg.get("simulation", {})
    sent_at = _now()
    feedback = simulator.apply_command(
        device_id, action_type, target_flow_m3_min, rated, processed,
        latency_ms=int(sim_cfg.get("latency_ms", 80)),
        feedback_delay_ms=int(sim_cfg.get("feedback_delay_ms", 120)))
    acked_at = _now()
    status = "executed" if feedback.get("success") else "failed"
    if use_db and command_id:
        repository.mark_command(command_id, status, feedback.get("message", ""),
                                sent_at=sent_at, acked_at=acked_at, feedback=feedback)
        repository.add_feedback(command_id, feedback)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    sla = cfg.get("sla", {})
    return {"status": "success", "accepted": True, "command_id": command_id,
            "device_id": device_id, "action_type": action_type,
            "target_flow_m3_min": target_flow_m3_min,
            "command_status": status, "feedback": feedback,
            "sent_at": sent_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "acked_at": acked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "elapsed_ms": round(elapsed_ms, 1),
            "sla_ok": {"command_dispatch": elapsed_ms / 1000 <= float(sla.get("command_dispatch_s", 3)),
                       "feedback_return": elapsed_ms / 1000 <= float(sla.get("feedback_return_s", 10))},
            "source": "simulated_plc"}


def execute_plan(cfg: dict, plan: dict, persist: bool = True) -> dict:
    """执行 B 的调度方案: 准入闸门 -> 逐机下发 -> 汇总反馈。"""
    t0 = time.perf_counter()
    allowed, gate_reason = _plan_gate(plan)
    plan_id = None
    if _persist_enabled(persist):
        plan_id = repository.save_dispatch_plan(plan)

    if not allowed:
        return {"status": "success", "source": "simulated_plc", "plan_id": plan_id,
                "execution_permission": plan.get("execution_permission",
                                                 "simulation_candidate_only"),
                "accepted": False, "command_count": 0, "results": [],
                "actual_total_power_kw": 0.0, "rejected_reason": gate_reason,
                "requires_human_review": True,
                "elapsed_seconds": round(time.perf_counter() - t0, 3)}

    actions = plan.get("device_actions", [])
    results = []
    total_power = 0.0
    for entry in actions:
        dev = entry.get("device_id")
        act = str(entry.get("action", "hold")).lower()
        target = entry.get("target_flow_m3_min")
        if act in ("start", "adjust"):
            action_type = "run" if entry.get("running_after") and target is None else "set_load"
        elif act == "stop":
            action_type, target = "stop", None
        else:  # hold
            continue
        res = plc_command(cfg, dev, action_type, target_flow_m3_min=target,
                          plan_id=plan_id, persist=persist)
        results.append({"device_id": dev, "requested": act,
                        "accepted": res.get("accepted", False),
                        "command_id": res.get("command_id"),
                        "command_status": res.get("command_status"),
                        "rejected_reason": res.get("rejected_reason"),
                        "feedback": res.get("feedback")})
        if res.get("feedback"):
            total_power += float(res["feedback"].get("actual_power_kw", 0.0))

    elapsed_s = time.perf_counter() - t0
    sla = cfg.get("sla", {})
    return {"status": "success", "source": "simulated_plc", "plan_id": plan_id,
            "execution_permission": plan.get("execution_permission",
                                             "simulation_candidate_only"),
            "accepted": True, "command_count": len(results), "results": results,
            "actual_total_power_kw": round(total_power, 1),
            "requires_human_review": True,
            "elapsed_seconds": round(elapsed_s, 3),
            "sla_ok": {"schedule_cycle": elapsed_s <= float(sla.get("workorder_generate_s", 30))}}


def work_order(cfg: dict, device_id: str, reason: str | None = None,
               priority: str = "中", detail: dict | None = None,
               persist: bool = True) -> dict:
    t0 = time.perf_counter()
    from air_station.models.health.service import health_json
    health = {d["device_id"]: d for d in health_json(
        processed_dir=cfg.get("paths", {}).get("processed_dir", DEFAULT_PROCESSED)
    ).get("devices", [])}
    text = eam.build_work_order_reason(device_id, health.get(device_id), reason)
    wo_id = None
    if _persist_enabled(persist):
        wo_id = repository.create_work_order(device_id, text, priority=priority,
                                             source="agent", detail=detail)
    elapsed_s = time.perf_counter() - t0
    sla = cfg.get("sla", {})
    return {"status": "success", "source": "simulated_eam", "work_order_id": wo_id,
            "device_id": device_id, "reason": text, "priority": priority,
            "elapsed_seconds": round(elapsed_s, 3),
            "sla_ok": elapsed_s <= float(sla.get("workorder_generate_s", 30))}


def control_json(action: str, payload: dict | None = None,
                 persist: bool = True, config_path: str = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    payload = payload or {}
    if action == "scada_query":
        return scada_query(cfg, payload.get("device_id"))
    if action == "scada_history":
        return scada_history(cfg, payload["device_id"], int(payload.get("minutes", 60)))
    if action == "plc_command":
        return plc_command(cfg, payload["device_id"], payload.get("action_type", "set_load"),
                           payload.get("target_flow_m3_min"), payload.get("plan_id"), persist)
    if action == "execute_plan":
        return execute_plan(cfg, payload["plan"], persist)
    if action == "eam_maintenance":
        snap = simulator.snapshot(cfg.get("paths", {}).get("processed_dir", DEFAULT_PROCESSED))
        remaining = {d["device_id"]: d.get("maintenance_remaining_hours")
                     for d in snap.get("devices", [])}
        return eam.maintenance_plan(payload.get("device_id"),
                                    cfg.get("paths", {}).get("device_archive", DEFAULT_ARCHIVE),
                                    remaining)
    if action == "work_order":
        return work_order(cfg, payload["device_id"], payload.get("reason"),
                          payload.get("priority", "中"), payload.get("detail"), persist)
    if action == "command_status":
        cmd = repository.get_command(payload["command_id"])
        if cmd is None:
            raise FileNotFoundError(f"[control] 指令不存在: {payload['command_id']}")
        return {"status": "success", "command": cmd}
    raise ValueError(f"[control] 不支持的控制动作: {action}")


if __name__ == "__main__":
    import json
    print(json.dumps(control_json("scada_query"), ensure_ascii=False, indent=2))
