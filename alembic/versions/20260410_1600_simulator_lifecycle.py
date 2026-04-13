"""add device_configs table + simulator lifecycle columns to runs

Revision ID: c4d5e6f7a8b9
Revises: b2f9a1c3d4e5
Create Date: 2026-04-10 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b2f9a1c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. New columns on runs for V2 auto-provisioning flow
    op.add_column("runs", sa.Column("device_type", sa.String(200), nullable=True))
    op.add_column("runs", sa.Column("os_version", sa.String(200), nullable=True))
    op.add_column("runs", sa.Column("app_file_path", sa.Text(), nullable=True))

    # 2. Admin-curated device configurations
    op.create_table(
        "device_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("device_type", sa.String(200), nullable=False),
        sa.Column("device_identifier", sa.String(300), nullable=False),
        sa.Column("os_version", sa.String(50), nullable=False),
        sa.Column("os_identifier", sa.String(300), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
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


def downgrade() -> None:
    op.drop_table("device_configs")
    op.drop_column("runs", "app_file_path")
    op.drop_column("runs", "os_version")
    op.drop_column("runs", "device_type")
