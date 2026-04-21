"""App event delivery log.

Revision ID: 20260422_events
Revises: 20260421_apps
Create Date: 2026-04-22
"""

import sqlalchemy as sa
from alembic import op

revision = "20260422_events"
down_revision = "20260421_apps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_event_deliveries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "installation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_installations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event", sa.String(100), nullable=False, index=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("app_event_deliveries")
