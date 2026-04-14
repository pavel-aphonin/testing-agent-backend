"""Add pbt_enabled flag to runs.

Revision ID: 20260414_pbt
Revises: 20260414_defc
Create Date: 2026-04-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20260414_pbt"
down_revision = "20260414_defc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "pbt_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "pbt_enabled")
