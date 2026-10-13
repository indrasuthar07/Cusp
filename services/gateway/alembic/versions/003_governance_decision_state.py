"""Persist governance inputs and signed decisions for deterministic retries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "003_governance_decision_state"
down_revision: str | None = "002_audit_event_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("action_attempts", sa.Column("provider_profile", sa.String(length=128), nullable=True))
    op.add_column("action_attempts", sa.Column("estimated_cost_micro_usd", sa.BigInteger(), nullable=True))
    op.add_column("action_attempts", sa.Column("progress", JSONB(), nullable=True))
    op.execute("UPDATE action_attempts SET provider_profile = '', estimated_cost_micro_usd = 0, progress = '{}'::jsonb")
    op.alter_column("action_attempts", "provider_profile", nullable=False)
    op.alter_column("action_attempts", "estimated_cost_micro_usd", nullable=False)
    op.alter_column("action_attempts", "progress", nullable=False)

    op.add_column("decisions", sa.Column("public_id", UUID(as_uuid=True), nullable=True))
    op.add_column("decisions", sa.Column("cost_provenance", sa.String(length=32), nullable=True))
    op.add_column("decisions", sa.Column("approved_profile", sa.String(length=128), nullable=True))
    op.add_column("decisions", sa.Column("policy_version", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("decisions", sa.Column("decision_token", sa.Text(), nullable=True))
    op.execute(
        "UPDATE decisions SET public_id = md5(id::text || ':' || clock_timestamp()::text)::uuid, "
        "cost_provenance = 'unknown', policy_version = 1"
    )
    op.alter_column("decisions", "public_id", nullable=False)
    op.alter_column("decisions", "cost_provenance", nullable=False)
    op.alter_column("decisions", "policy_version", nullable=False)
    op.create_unique_constraint("uq_decisions_public_id", "decisions", ["public_id"])


def downgrade() -> None:
    op.drop_constraint("uq_decisions_public_id", "decisions", type_="unique")
    for column in ("decision_token", "expires_at", "policy_version", "approved_profile", "cost_provenance", "public_id"):
        op.drop_column("decisions", column)
    for column in ("progress", "estimated_cost_micro_usd", "provider_profile"):
        op.drop_column("action_attempts", column)