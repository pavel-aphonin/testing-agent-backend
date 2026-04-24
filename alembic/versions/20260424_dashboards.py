"""Dashboards, widgets and per-user access grants.

Three tables:
  - dashboards:            one system + any number of user ones per workspace
  - dashboard_widgets:     widget configs bound to a dashboard
  - dashboard_permissions: per-user grants for user-flavor dashboards

At most one system dashboard per workspace — enforced with a partial
unique index (``is_system=true``), not a regular ``UNIQUE(workspace_id)``
because user dashboards share the workspace column.

Also seeds a system dashboard into every existing workspace so we
don't have ``NULL``-state UIs after the upgrade.

Revision ID: 20260424_dashboards
Revises: 20260423_avatar_nav
Create Date: 2026-04-24
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260424_dashboards"
down_revision = "20260423_avatar_nav"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(20), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean,
            nullable=False,
            server_default="false",
            index=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Exactly one system dashboard per workspace.
    op.execute(
        "CREATE UNIQUE INDEX uq_dash_one_system_per_ws "
        "ON dashboards (workspace_id) WHERE is_system = TRUE"
    )

    op.create_table(
        "dashboard_widgets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dashboard_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("widget_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="Виджет"),
        sa.Column("datasource_code", sa.String(60), nullable=True),
        sa.Column("datasource_params", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("chart_options", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("grid_x", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grid_y", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grid_w", sa.Integer, nullable=False, server_default="6"),
        sa.Column("grid_h", sa.Integer, nullable=False, server_default="4"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "dashboard_permissions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dashboard_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("dashboard_id", "user_id", name="uq_dash_perm"),
    )

    # Backfill: one system dashboard per existing workspace, named
    # after the workspace, with 4 starter widgets (see list below).
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM workspaces")).all()
    for ws_id, ws_name in rows:
        dash_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO dashboards (id, workspace_id, name, icon, "
                "is_system, sort_order) VALUES "
                "(cast(:id as uuid), cast(:ws as uuid), :n, '📊', TRUE, 0)"
            ),
            {"id": str(dash_id), "ws": str(ws_id), "n": ws_name},
        )
        # Starter widgets — see SYSTEM_WIDGETS below for semantics.
        for w in SYSTEM_WIDGETS:
            conn.execute(
                sa.text(
                    "INSERT INTO dashboard_widgets "
                    "(id, dashboard_id, widget_type, title, datasource_code, "
                    "datasource_params, chart_options, grid_x, grid_y, grid_w, grid_h) "
                    "VALUES (cast(:id as uuid), cast(:d as uuid), :t, :title, "
                    ":ds, cast(:dp as jsonb), cast(:co as jsonb), :x, :y, :w, :h)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "d": str(dash_id),
                    "t": w["widget_type"],
                    "title": w["title"],
                    "ds": w["datasource_code"],
                    "dp": w.get("datasource_params_json", "null"),
                    "co": w.get("chart_options_json", "null"),
                    "x": w["grid_x"],
                    "y": w["grid_y"],
                    "w": w["grid_w"],
                    "h": w["grid_h"],
                },
            )


# Starter widgets for a fresh workspace's system dashboard. Chosen to
# be the "what is happening with my tests right now" view:
#   Top row: two equal pies showing status + defect priority
#   Middle:  wide line chart with runs over the past 14 days
#   Bottom:  wide table with the latest 10 runs
SYSTEM_WIDGETS = [
    {
        "widget_type": "pie",
        "title": "Запуски по статусам",
        "datasource_code": "runs.by_status",
        "datasource_params_json": "{}",
        "chart_options_json": "null",
        "grid_x": 0, "grid_y": 0, "grid_w": 6, "grid_h": 4,
    },
    {
        "widget_type": "donut",
        "title": "Дефекты по приоритетам",
        "datasource_code": "defects.by_priority",
        "datasource_params_json": "{}",
        "chart_options_json": "null",
        "grid_x": 6, "grid_y": 0, "grid_w": 6, "grid_h": 4,
    },
    {
        "widget_type": "line",
        "title": "Запуски за последние 14 дней",
        "datasource_code": "runs.by_day",
        "datasource_params_json": '{"days": 14}',
        "chart_options_json": "null",
        "grid_x": 0, "grid_y": 4, "grid_w": 12, "grid_h": 4,
    },
    {
        "widget_type": "table",
        "title": "Последние запуски",
        "datasource_code": "runs.recent",
        "datasource_params_json": '{"limit": 10}',
        "chart_options_json": "null",
        "grid_x": 0, "grid_y": 8, "grid_w": 12, "grid_h": 5,
    },
]


def downgrade() -> None:
    op.drop_table("dashboard_permissions")
    op.drop_table("dashboard_widgets")
    op.execute("DROP INDEX IF EXISTS uq_dash_one_system_per_ws")
    op.drop_table("dashboards")
