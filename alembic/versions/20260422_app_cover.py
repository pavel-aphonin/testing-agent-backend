"""App cover image + per-user UI preferences for installations.

- app_packages.cover_path: widescreen hero banner image (optional)
- app_installation_user_prefs: per-user toggles for an installation
  (e.g. hide from sidebar). Keyed by (user, installation).

Revision ID: 20260422_cover
Revises: 20260422_events
Create Date: 2026-04-22
"""

import sqlalchemy as sa
from alembic import op

revision = "20260422_cover"
down_revision = "20260422_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "app_packages",
        sa.Column("cover_path", sa.Text, nullable=True),
    )

    op.create_table(
        "app_installation_user_prefs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "installation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_installations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Free-form JSONB so we can add more toggles later without migrations.
        sa.Column("prefs", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "installation_id", name="uq_inst_user_prefs"),
    )


def downgrade() -> None:
    op.drop_table("app_installation_user_prefs")
    op.drop_column("app_packages", "cover_path")
