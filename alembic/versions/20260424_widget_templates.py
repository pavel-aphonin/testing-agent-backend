"""Widget templates — reusable widget presets per workspace.

Stores a widget's config (type, datasource, params, options) + some
presentation metadata (name, icon, description, default size). When a
user clicks "Add widget → From template X" on a dashboard, we materialize
a DashboardWidget from the template.

Revision ID: 20260424_widget_tpl
Revises: 20260424_dashboards
Create Date: 2026-04-24
"""

import sqlalchemy as sa
from alembic import op

revision = "20260424_widget_tpl"
down_revision = "20260424_dashboards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "widget_templates",
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
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(20), nullable=True),
        sa.Column("widget_type", sa.String(30), nullable=False),
        sa.Column("datasource_code", sa.String(60), nullable=True),
        sa.Column("datasource_params", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("chart_options", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("default_w", sa.Integer, nullable=False, server_default="6"),
        sa.Column("default_h", sa.Integer, nullable=False, server_default="4"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("widget_templates")
