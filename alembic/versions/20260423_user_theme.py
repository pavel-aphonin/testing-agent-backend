"""Per-user theme_overrides JSONB on agent_settings.

Allows a user to override any subset of the system-level theme tokens
just for themselves. Same shape as SystemBranding.theme_tokens.

Revision ID: 20260423_user_thm
Revises: 20260423_brand_tok
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_user_thm"
down_revision = "20260423_brand_tok"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_settings",
        sa.Column("theme_overrides", sa.dialects.postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_settings", "theme_overrides")
