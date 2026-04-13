"""add language column to agent_settings

Revision ID: b2f9a1c3d4e5
Revises: a3b8c4d5e6f7
Create Date: 2026-04-08 15:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b2f9a1c3d4e5"
down_revision: Union[str, None] = "a3b8c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_settings",
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=False,
            server_default="en",
        ),
    )
    op.alter_column("agent_settings", "language", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_settings", "language")
