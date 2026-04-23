"""Avatar on users + hidden_nav_items on agent_settings.

Two independent additions tied together by the same profile iteration:

- ``users.avatar_path`` — relative path (under ``app_uploads_dir``) to
  the user's uploaded avatar. NULL → UI uses the default circle with
  first letter of email.
- ``agent_settings.hidden_nav_items`` — JSONB array of built-in nav
  keys the user chose to hide ("/devices", "/admin/users", …). NULL
  or [] → show everything their permissions allow.

Revision ID: 20260423_avatar_nav
Revises: 20260423_release
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_avatar_nav"
down_revision = "20260423_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_path", sa.Text, nullable=True))
    op.add_column(
        "agent_settings",
        sa.Column("hidden_nav_items", sa.dialects.postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_settings", "hidden_nav_items")
    op.drop_column("users", "avatar_path")
