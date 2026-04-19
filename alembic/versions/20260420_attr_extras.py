"""Extend attributes: new data_types (date, link, member) + is_required flag.

The data_type column stays a free-form string — adding new types is just
about teaching the validators on both sides. is_required is a new boolean
column with default false so existing rows aren't affected.

Revision ID: 20260420_attrx
Revises: 20260420_attr
Create Date: 2026-04-20
"""

import sqlalchemy as sa
from alembic import op

revision = "20260420_attrx"
down_revision = "20260420_attr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attributes",
        sa.Column(
            "is_required",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("attributes", "is_required")
