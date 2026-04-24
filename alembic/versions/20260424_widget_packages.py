"""Widget packages — custom user-authored iframe widgets.

Phase 3b: users can upload a full HTML blob + manifest describing which
data sources it consumes and what config fields to expose. The dashboard
renders these in a sandboxed iframe; the parent posts ``{widget, data}``
and the iframe draws whatever it wants (D3, raw canvas, custom tables,
…). Workspace-scoped — a package visible in workspace A isn't visible
in workspace B.

Revision ID: 20260424_widget_pkg
Revises: 20260424_widget_tpl
Create Date: 2026-04-24
"""

import sqlalchemy as sa
from alembic import op

revision = "20260424_widget_pkg"
down_revision = "20260424_widget_tpl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_packages",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(20), nullable=True),
        sa.Column("version", sa.String(40), nullable=False, server_default="0.1.0"),
        sa.Column(
            "manifest",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("html_source", sa.Text, nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "code", name="uq_widget_pkg_code"),
    )


def downgrade() -> None:
    op.drop_table("widget_packages")
