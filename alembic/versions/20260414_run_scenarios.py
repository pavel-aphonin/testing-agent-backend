"""Add scenario_ids to runs.

Revision ID: 20260414_scen
Revises: 20260413_embed
Create Date: 2026-04-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20260414_scen"
down_revision = "20260413_embed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("scenario_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "scenario_ids")
