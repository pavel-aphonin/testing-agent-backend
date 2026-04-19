"""Tree-structured dictionaries: parent_id + is_group on roles/workspaces.

Adds two columns to every "dictionary" table:
  - parent_id: self-FK, allows unlimited nesting
  - is_group: when true, this row is a folder (no functional payload)

Same shape for roles, workspaces, and the upcoming attributes table.

Revision ID: 20260420_tree
Revises: 20260420_notif
Create Date: 2026-04-20
"""

import sqlalchemy as sa
from alembic import op

revision = "20260420_tree"
down_revision = "20260420_notif"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("roles", "workspaces"):
        op.add_column(
            table,
            sa.Column(
                "parent_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey(f"{table}.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "is_group",
                sa.Boolean,
                nullable=False,
                server_default="false",
            ),
        )


def downgrade() -> None:
    for table in ("roles", "workspaces"):
        op.drop_column(table, "is_group")
        op.drop_column(table, "parent_id")
