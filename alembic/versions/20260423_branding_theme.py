"""Add theme primary colors to system_branding.

Two columns — one per theme — so a brand can tune the hue for light
and dark mode independently. Stored as hex strings so the frontend
can feed them straight into the AntD ConfigProvider without parsing.

Revision ID: 20260423_brand_thm
Revises: 20260423_brand_fav
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_brand_thm"
down_revision = "20260423_brand_fav"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_branding",
        sa.Column("primary_color_light", sa.String(9), nullable=True),
    )
    op.add_column(
        "system_branding",
        sa.Column("primary_color_dark", sa.String(9), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_branding", "primary_color_dark")
    op.drop_column("system_branding", "primary_color_light")
