"""Initial Cusp schema - all tables
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Enum types ──────────────────────────────────────────────────────────

membership_role = sa.Enum(
    "owner", "admin", "member", "viewer", name="membership_role", create_type=False
)
provider_kind = sa.Enum(
    "fake", "ollama", "gemini", "groq", "huggingface", "openai", "anthropic",
    name="provider_kind", create_type=False
)
session_status = sa.Enum(
    "pending", "running", "paused", "completed", "terminated", "failed",
    name="session_status", create_type=False
)
decision_verdict = sa.Enum(
    "allow", "downgrade", "pause", "escalate", "kill",
    name="decision_verdict", create_type=False
)
cost_source = sa.Enum(
    "provider_reported", "estimated", "virtual", "unknown",
    name="cost_source", create_type=False
)
energy_quality = sa.Enum(
    "measured", "estimated", "unavailable",
    name="energy_quality", create_type=False
)


def upgrade() -> None:
    # Create enum types first
    membership_role.create(op.get_bind(), checkfirst=True)
    provider_kind.create(op.get_bind(), checkfirst=True)
    session_status.create(op.get_bind(), checkfirst=True)
    decision_verdict.create(op.get_bind(), checkfirst=True)
    cost_source.create(op.get_bind(), checkfirst=True)
    energy_quality.create(op.get_bind(), checkfirst=True)

    # organizations
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_disabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("oidc_subject", sa.String(255), nullable=True, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # memberships
    op.create_table(
        "memberships",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
    )

    # agent_registrations 
    op.create_table(
        "agent_registrations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("is_disabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", name="uq_agent_org_name"),
    )

    # service_tokens 
    op.create_table(
        "service_tokens",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("agent_id", sa.BigInteger, sa.ForeignKey("agent_registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("scopes", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # model_profiles 
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_kind", provider_kind, nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("capabilities", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("cost_config", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_model_profiles_provider_enabled", "model_profiles", ["provider_kind", "is_enabled"])

    # policy_sets 
    op.create_table(
        "policy_sets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_policy_sets_org_active", "policy_sets", ["org_id", "is_active"])

    # policy_versions 
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("policy_set_id", sa.BigInteger, sa.ForeignKey("policy_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("rules", JSONB, nullable=False),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # sessions 
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.BigInteger, sa.ForeignKey("agent_registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", session_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("budget_cap_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0"),
                  comment="Hard budget cap in micro-USD (1 USD = 1_000_000)"),
        sa.Column("parent_session_id", sa.BigInteger, sa.ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_org_status_started", "sessions", ["org_id", "status", "started_at"])

    # action_attempts 
    op.create_table(
        "action_attempts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("session_id", sa.BigInteger, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_action_attempts_session_seq", "action_attempts", ["session_id", "sequence_number"])

    # usage_records 
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("action_id", sa.BigInteger, sa.ForeignKey("action_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_kind", provider_kind, nullable=False),
        sa.Column("model_profile_name", sa.String(255), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_virtual_cost_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0"),
                  comment="Virtual cost estimate in micro-USD"),
        sa.Column("actual_virtual_cost_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0"),
                  comment="Actual virtual cost in micro-USD"),
        sa.Column("provider_reported_cost_micro_usd", sa.BigInteger, nullable=True,
                  comment="Provider-reported cost in micro-USD — null if unavailable"),
        sa.Column("cost_source", cost_source, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_per_second", sa.Float, nullable=True),
        sa.Column("energy_wh", sa.Float, nullable=True,
                  comment="Energy consumption in Wh — null, never zero, when unavailable"),
        sa.Column("energy_quality", energy_quality, nullable=False, server_default=sa.text("'unavailable'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # decisions 
    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.BigInteger, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_id", sa.BigInteger, sa.ForeignKey("action_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", decision_verdict, nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("ev_score", sa.Float, nullable=True),
        sa.Column("loop_score", sa.Float, nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("policy_version_id", sa.BigInteger, sa.ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisions_session_created", "decisions", ["session_id", "created_at"])

    # audit_events 
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("correlation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'"),
                  comment="Redacted event payload — no raw prompts or source code"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id"])

    # daily_spend_aggregates 
    op.create_table(
        "daily_spend_aggregates",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_kind", provider_kind, nullable=False),
        sa.Column("model_profile_name", sa.String(255), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_actions", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("total_output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("total_virtual_cost_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("total_provider_cost_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "provider_kind", "model_profile_name", "date",
                            name="uq_daily_spend_org_provider_model_date"),
    )
    op.create_index("ix_daily_spend_org_provider_model_date",
                    "daily_spend_aggregates", ["org_id", "provider_kind", "model_profile_name", "date"])


def downgrade() -> None:
    op.drop_table("daily_spend_aggregates")
    op.drop_table("audit_events")
    op.drop_table("decisions")
    op.drop_table("usage_records")
    op.drop_table("action_attempts")
    op.drop_table("sessions")
    op.drop_table("policy_versions")
    op.drop_table("policy_sets")
    op.drop_table("model_profiles")
    op.drop_table("service_tokens")
    op.drop_table("agent_registrations")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")

    # Drop enum types
    sa.Enum(name="energy_quality").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="cost_source").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="decision_verdict").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="session_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="provider_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="membership_role").drop(op.get_bind(), checkfirst=True)