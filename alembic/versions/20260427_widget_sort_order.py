"""Add sort_order to widget_templates and widget_packages.

Both tables sorted by ``created_at DESC`` until now. After ten or so
entries that becomes useless — there's no way to bubble «часто
используемое» to the top. Adds a manual ordering column that defaults
to a large number so existing rows fall to the end on first sort, then
a one-time backfill spreads values to give DnD/up-down actions some
room to insert between (step = 10).

Revision ID: 20260427_widget_sort
Revises: 20260424_widget_pkg
Create Date: 2026-04-27
"""

import sqlalchemy as sa
from alembic import op

revision = "20260427_widget_sort"
down_revision = "20260424_widget_pkg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("widget_templates", "widget_packages"):
        op.add_column(
            table,
            sa.Column(
                "sort_order",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
        )
        # Backfill: spread existing rows by created_at order with
        # gaps of 10. Postgres-only via window function; if you need
        # generic SQL split into a SELECT + per-row UPDATE.
        op.execute(
            f"""
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY workspace_id ORDER BY created_at
                     ) AS rn
              FROM {table}
            )
            UPDATE {table}
            SET sort_order = (ranked.rn - 1) * 10
            FROM ranked
            WHERE {table}.id = ranked.id;
            """
        )
        op.create_index(
            f"ix_{table}_workspace_sort",
            table,
            ["workspace_id", "sort_order"],
        )


def downgrade() -> None:
    for table in ("widget_templates", "widget_packages"):
        op.drop_index(f"ix_{table}_workspace_sort", table_name=table)
        op.drop_column(table, "sort_order")
