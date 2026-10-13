"""Make audit event persistence idempotent across worker restarts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_audit_event_idempotency"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events", sa.Column("event_id", sa.String(length=255), nullable=True)
    )
    op.execute(
        "UPDATE audit_events SET event_id = md5(id::text || ':' || clock_timestamp()::text) "
        "WHERE event_id IS NULL"
    )
    op.alter_column("audit_events", "event_id", nullable=False)
    op.create_unique_constraint(
        "uq_audit_events_event_id", "audit_events", ["event_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_audit_events_event_id", "audit_events", type_="unique")
    op.drop_column("audit_events", "event_id")
