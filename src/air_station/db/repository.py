"""仓储层: 封装数据库读写, 供控制闭环与 Agent 使用。"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select

from air_station.db import base as db_base
from air_station.db.models import (AgentSession, ControlCommand, DispatchPlan,
                                   ExecutionFeedback, WorkOrder)


def ensure_db():
    db_base.init_db()


@contextmanager
def session_scope():
    factory = db_base.get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def save_agent_session(user_input: str, intent: str, answer: str,
                       tools_used: list[str], response: dict) -> str:
    with session_scope() as s:
        row = AgentSession(user_input=user_input, intent=intent, answer=answer,
                           tools_used=json.dumps(tools_used, ensure_ascii=False),
                           response_json=json.dumps(response, ensure_ascii=False))
        s.add(row)
        s.flush()
        return row.id


def list_agent_sessions(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(AgentSession).order_by(AgentSession.created_at.desc())
                         .limit(limit)).all()
        return [{"id": r.id, "created_at": _iso(r.created_at), "user_input": r.user_input,
                 "intent": r.intent, "answer": r.answer,
                 "tools_used": json.loads(r.tools_used or "[]")} for r in rows]


def get_agent_session(session_id: str) -> dict | None:
    with session_scope() as s:
        r = s.get(AgentSession, session_id)
        if r is None:
            return None
        return {"id": r.id, "created_at": _iso(r.created_at), "user_input": r.user_input,
                "intent": r.intent, "answer": r.answer,
                "tools_used": json.loads(r.tools_used or "[]"),
                "response": json.loads(r.response_json or "{}")}


def save_dispatch_plan(plan: dict) -> str:
    """持久化一份调度方案(B 输出或等价结构均可, 字段缺失时安全退化)。"""
    def _f(*keys, default=0.0):
        for k in keys:
            v = plan.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return default

    actions = plan.get("device_actions") or plan.get("schedule") or []
    combo = plan.get("target_combination") or [a.get("device_id") for a in actions]
    base_cmp = plan.get("baseline_comparison", {}) or {}
    with session_scope() as s:
        row = DispatchPlan(
            horizon_minutes=int(plan.get("horizon_minutes", 15) or 15),
            demand_flow_m3_min=_f("required_flow_m3_min", "demand_flow_m3_min"),
            demand_source=str(plan.get("mode") or plan.get("demand", {}).get("source", "b_api")),
            pressure_setpoint_mpa=_f("pressure_setpoint_mpa", default=0.66),
            running_count=len(combo),
            total_power_kw=_f("estimated_power_kw", "total_power_kw"),
            baseline_power_kw=_f("baseline_power_kw", default=0.0),
            saving_pct=_f("energy_saving_pct", default=0.0),
            plan_json=json.dumps(plan, ensure_ascii=False),
        )
        if base_cmp:
            row.plan_json = json.dumps(plan, ensure_ascii=False)
        s.add(row)
        s.flush()
        return row.id


def list_dispatch_plans(limit: int = 20) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(DispatchPlan).order_by(DispatchPlan.created_at.desc())
                         .limit(limit)).all()
        return [{"id": r.id, "created_at": _iso(r.created_at),
                 "horizon_minutes": r.horizon_minutes,
                 "demand_flow_m3_min": r.demand_flow_m3_min,
                 "running_count": r.running_count,
                 "total_power_kw": r.total_power_kw,
                 "baseline_power_kw": r.baseline_power_kw,
                 "saving_pct": r.saving_pct} for r in rows]


def create_control_command(device_id: str, action: str, target_flow_m3_min: float,
                           load_ratio: float, plan_id: str | None = None) -> str:
    with session_scope() as s:
        row = ControlCommand(device_id=device_id, action=action,
                             target_flow_m3_min=target_flow_m3_min,
                             load_ratio=load_ratio, plan_id=plan_id, status="created")
        s.add(row)
        s.flush()
        return row.id


def mark_command(command_id: str, status: str, message: str = "",
                 sent_at: datetime | None = None, acked_at: datetime | None = None,
                 feedback: dict | None = None) -> None:
    with session_scope() as s:
        row = s.get(ControlCommand, command_id)
        if row is None:
            return
        row.status = status
        if message:
            row.message = message
        if sent_at is not None:
            row.sent_at = sent_at
        if acked_at is not None:
            row.acked_at = acked_at
        if feedback is not None:
            row.feedback_json = json.dumps(feedback, ensure_ascii=False)


def add_feedback(command_id: str, feedback: dict) -> str:
    with session_scope() as s:
        row = ExecutionFeedback(
            command_id=command_id,
            success=1 if feedback.get("success", True) else 0,
            actual_flow_m3_min=float(feedback.get("actual_flow_m3_min", 0.0)),
            actual_power_kw=float(feedback.get("actual_power_kw", 0.0)),
            feedback_json=json.dumps(feedback, ensure_ascii=False),
        )
        s.add(row)
        s.flush()
        return row.id


def get_command(command_id: str) -> dict | None:
    with session_scope() as s:
        r = s.get(ControlCommand, command_id)
        if r is None:
            return None
        return {"id": r.id, "plan_id": r.plan_id, "device_id": r.device_id,
                "action": r.action, "target_flow_m3_min": r.target_flow_m3_min,
                "load_ratio": r.load_ratio, "status": r.status, "message": r.message,
                "created_at": _iso(r.created_at), "sent_at": _iso(r.sent_at),
                "acked_at": _iso(r.acked_at),
                "feedback": json.loads(r.feedback_json or "{}")}


def list_commands(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ControlCommand)
                         .order_by(ControlCommand.created_at.desc()).limit(limit)).all()
        return [{"id": r.id, "device_id": r.device_id, "action": r.action,
                 "target_flow_m3_min": r.target_flow_m3_min, "load_ratio": r.load_ratio,
                 "status": r.status, "created_at": _iso(r.created_at)} for r in rows]


def create_work_order(device_id: str, reason: str, priority: str = "中",
                      source: str = "agent", detail: dict | None = None) -> str:
    with session_scope() as s:
        row = WorkOrder(device_id=device_id, reason=reason, priority=priority,
                        source=source, detail_json=json.dumps(detail or {}, ensure_ascii=False))
        s.add(row)
        s.flush()
        return row.id


def list_work_orders(limit: int = 50) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(WorkOrder).order_by(WorkOrder.created_at.desc())
                         .limit(limit)).all()
        return [{"id": r.id, "created_at": _iso(r.created_at), "device_id": r.device_id,
                 "reason": r.reason, "priority": r.priority, "status": r.status,
                 "source": r.source, "detail": json.loads(r.detail_json or "{}")} for r in rows]
