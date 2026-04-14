"""Add title column to runs.

Revision ID: 20260415_title
Revises: 20260414_pbt
Create Date: 2026-04-15
"""

import sqlalchemy as sa
from alembic import op

revision = "20260415_title"
down_revision = "20260414_pbt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "title")
