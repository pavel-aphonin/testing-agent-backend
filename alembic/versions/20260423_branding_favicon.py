"""Add favicon_path to system_branding.

Browser-tab icon gets its own column (not reused from ``logo_path``)
because favicons have different practical constraints: square, tiny,
typically `.ico`/`.png`, sometimes tinted for dark mode. The sidebar
logo can be any aspect ratio and usually SVG.

Revision ID: 20260423_brand_fav
Revises: 20260423_branding
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_brand_fav"
down_revision = "20260423_branding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_branding",
        sa.Column("favicon_path", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_branding", "favicon_path")
