"""add graph_library column to agent_settings

Revision ID: 9f1e7c2a4b80
Revises: ced2af5e0bc1
Create Date: 2026-04-08 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f1e7c2a4b80"
down_revision: Union[str, None] = "ced2af5e0bc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New column gets a server_default so existing rows are filled in-place
    # without an explicit UPDATE. We then keep the application-level default
    # in the model and drop the server_default to avoid drift.
    op.add_column(
        "agent_settings",
        sa.Column(
            "graph_library",
            sa.String(length=20),
            nullable=False,
            server_default="react-flow",
        ),
    )
    op.alter_column("agent_settings", "graph_library", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_settings", "graph_library")
