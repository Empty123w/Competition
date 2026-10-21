"""SQLAlchemy ORM 模型 (阶段 C)。

覆盖: Agent 会话、调度方案、控制指令、执行反馈、工单。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from air_station.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    import uuid
    return uuid.uuid4().hex


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    user_input: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(32), default="unknown")
    answer: Mapped[str] = mapped_column(Text, default="")
    tools_used: Mapped[str] = mapped_column(Text, default="")
    response_json: Mapped[str] = mapped_column(Text, default="{}")


class DispatchPlan(Base):
    __tablename__ = "dispatch_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    horizon_minutes: Mapped[int] = mapped_column(Integer, default=15)
    demand_flow_m3_min: Mapped[float] = mapped_column(Float, default=0.0)
    demand_source: Mapped[str] = mapped_column(String(16), default="forecast")
    pressure_setpoint_mpa: Mapped[float] = mapped_column(Float, default=0.66)
    running_count: Mapped[int] = mapped_column(Integer, default=0)
    total_power_kw: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_power_kw: Mapped[float] = mapped_column(Float, default=0.0)
    saving_pct: Mapped[float] = mapped_column(Float, default=0.0)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")


class ControlCommand(Base):
    __tablename__ = "control_commands"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    device_id: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    target_flow_m3_min: Mapped[float] = mapped_column(Float, default=0.0)
    load_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="created")
    message: Mapped[str] = mapped_column(Text, default="")
    feedback_json: Mapped[str] = mapped_column(Text, default="{}")


class ExecutionFeedback(Base):
    __tablename__ = "execution_feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    command_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    success: Mapped[int] = mapped_column(Integer, default=1)
    actual_flow_m3_min: Mapped[float] = mapped_column(Float, default=0.0)
    actual_power_kw: Mapped[float] = mapped_column(Float, default=0.0)
    feedback_json: Mapped[str] = mapped_column(Text, default="{}")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    device_id: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="中")
    status: Mapped[str] = mapped_column(String(16), default="open")
    source: Mapped[str] = mapped_column(String(32), default="agent")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
