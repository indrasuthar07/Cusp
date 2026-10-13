"""
Cusp Worker - SQLAlchemy ORM models for event persistence and metrics.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    """Append-only audit event record matching gateway schema."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_correlation", "correlation_id"),
        UniqueConstraint("event_id", name="uq_audit_events_event_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    org_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DailySpendAggregate(Base):
    """Daily aggregated spend and token counts."""

    __tablename__ = "daily_spend_aggregates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    model_profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_actions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_output_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_virtual_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_provider_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
