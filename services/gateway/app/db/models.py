"""
Cusp Gateway - SQLAlchemy ORM models.
"""

from __future__ import annotations
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Base 
class Base(DeclarativeBase):
    """Shared declarative base for all Cusp models."""
    pass


# Enums 
class MembershipRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class ProviderKind(str, enum.Enum):
    fake = "fake"
    ollama = "ollama"
    gemini = "gemini"
    groq = "groq"
    huggingface = "huggingface"
    openai = "openai"
    anthropic = "anthropic"


class SessionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    terminated = "terminated"
    failed = "failed"


class DecisionVerdict(str, enum.Enum):
    allow = "allow"
    downgrade = "downgrade"
    pause = "pause"
    escalate = "escalate"
    kill = "kill"


class CostSource(str, enum.Enum):
    """How confident we are in the cost figure."""
    provider_reported = "provider_reported"
    estimated = "estimated"
    virtual = "virtual"
    unknown = "unknown"


class EnergyQuality(str, enum.Enum):
    measured = "measured"
    estimated = "estimated"
    unavailable = "unavailable"


# Organizations 
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    memberships: Mapped[list[Membership]] = relationship(back_populates="organization")
    agents: Mapped[list[AgentRegistration]] = relationship(back_populates="organization")
    sessions: Mapped[list[Session]] = relationship(back_populates="organization")
    policy_sets: Mapped[list[PolicySet]] = relationship(back_populates="organization")
    model_profiles: Mapped[list[ModelProfile]] = relationship(back_populates="organization")


# Users 
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


#  Memberships 
class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        SAEnum(MembershipRole, name="membership_role"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


# Agent Registrations 
class AgentRegistration(Base):
    __tablename__ = "agent_registrations"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_agent_org_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="agents")
    service_tokens: Mapped[list[ServiceToken]] = relationship(back_populates="agent")
    sessions: Mapped[list[Session]] = relationship(back_populates="agent")


# Service Tokens 
class ServiceToken(Base):
    __tablename__ = "service_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent_registrations.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    scopes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped[AgentRegistration] = relationship(back_populates="service_tokens")


# Model Profiles 
class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        Index("ix_model_profiles_provider_enabled", "provider_kind", "is_enabled"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_kind: Mapped[ProviderKind] = mapped_column(
        SAEnum(ProviderKind, name="provider_kind"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    cost_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="model_profiles")


# Policy Sets & Versions 
class PolicySet(Base):
    __tablename__ = "policy_sets"
    __table_args__ = (
        Index("ix_policy_sets_org_active", "org_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="policy_sets")
    versions: Mapped[list[PolicyVersion]] = relationship(back_populates="policy_set")


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    policy_set_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("policy_sets.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    policy_set: Mapped[PolicySet] = relationship(back_populates="versions")


# Sessions (Runs) 
class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_org_status_started", "org_id", "status", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agent_registrations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status"), default=SessionStatus.pending, nullable=False
    )
    budget_cap_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False,
        comment="Hard budget cap in micro-USD (1 USD = 1_000_000)"
    )
    parent_session_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped[Organization] = relationship(back_populates="sessions")
    agent: Mapped[AgentRegistration] = relationship(back_populates="sessions")
    actions: Mapped[list[ActionAttempt]] = relationship(back_populates="session")


# Action Attempts (append-only) 
class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    __table_args__ = (
        Index("ix_action_attempts_session_seq", "session_id", "sequence_number"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[Session] = relationship(back_populates="actions")
    usage_records: Mapped[list[UsageRecord]] = relationship(back_populates="action")
    decisions: Mapped[list[Decision]] = relationship(back_populates="action")


# Usage Records (append-only) 
class UsageRecord(Base):
    """
    Critical cost tracking — every field from spec §7.

    All currency in micro-USD (integer). No floats ever.
    """
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("action_attempts.id", ondelete="CASCADE"), nullable=False
    )
    provider_kind: Mapped[ProviderKind] = mapped_column(
        SAEnum(ProviderKind, name="provider_kind", create_type=False), nullable=False
    )
    model_profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_virtual_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False,
        comment="Virtual cost estimate in micro-USD"
    )
    actual_virtual_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False,
        comment="Actual virtual cost in micro-USD"
    )
    provider_reported_cost_micro_usd: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
        comment="Provider-reported cost in micro-USD — null if unavailable"
    )
    cost_source: Mapped[CostSource] = mapped_column(
        SAEnum(CostSource, name="cost_source"), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_per_second: Mapped[float | None] = mapped_column(nullable=True)
    energy_wh: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="Energy consumption in Wh — null, never zero, when unavailable"
    )
    energy_quality: Mapped[EnergyQuality] = mapped_column(
        SAEnum(EnergyQuality, name="energy_quality"), default=EnergyQuality.unavailable, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    action: Mapped[ActionAttempt] = relationship(back_populates="usage_records")


# Decisions (append-only) 
class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_session_created", "session_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    action_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("action_attempts.id", ondelete="CASCADE"), nullable=False
    )
    verdict: Mapped[DecisionVerdict] = mapped_column(
        SAEnum(DecisionVerdict, name="decision_verdict"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ev_score: Mapped[float | None] = mapped_column(nullable=True)
    loop_score: Mapped[float | None] = mapped_column(nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    action: Mapped[ActionAttempt] = relationship(back_populates="decisions")


# Audit Events (append-only) 
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False,
        comment="Redacted event payload — no raw prompts or source code"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Daily Spend Aggregates 
class DailySpendAggregate(Base):
    """Pre-computed daily rollup for analytics — not used in hot path."""
    __tablename__ = "daily_spend_aggregates"
    __table_args__ = (
        UniqueConstraint("org_id", "provider_kind", "model_profile_name", "date",
                         name="uq_daily_spend_org_provider_model_date"),
        Index("ix_daily_spend_org_provider_model_date",
              "org_id", "provider_kind", "model_profile_name", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provider_kind: Mapped[ProviderKind] = mapped_column(
        SAEnum(ProviderKind, name="provider_kind", create_type=False), nullable=False
    )
    model_profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_actions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_virtual_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    total_provider_cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )