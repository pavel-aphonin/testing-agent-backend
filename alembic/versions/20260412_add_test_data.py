"""add test_data table

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_data",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_test_data_created_by_user_id",
        "test_data",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_data_created_by_user_id", table_name="test_data")
    op.drop_table("test_data")
