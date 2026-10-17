"""Persist non-authoritative learned shadow recommendations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

revision: str = "004_shadow_recommendations"
down_revision: str | None = "003_governance_decision_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Reuse the PostgreSQL enum created by migration 001.  The generic
    # SQLAlchemy Enum ignores create_type=False when attached to create_table.
    decision_verdict = ENUM(
        "allow", "downgrade", "pause", "escalate", "kill",
        name="decision_verdict", create_type=False,
    )
    op.create_table(
        "shadow_recommendations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("action_id", sa.BigInteger(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("recommended_verdict", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("deterministic_verdict", decision_verdict, nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("feature_snapshot", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["action_id"], ["action_attempts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("action_id", "model_name", "model_version", name="uq_shadow_action_model"),
    )
    op.create_index("ix_shadow_recommendations_org_created", "shadow_recommendations", ["org_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_shadow_recommendations_org_created", table_name="shadow_recommendations")
    op.drop_table("shadow_recommendations")
