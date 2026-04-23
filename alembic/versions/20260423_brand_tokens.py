"""Expand branding theme to a JSONB token blob.

Replaces ``primary_color_light`` / ``primary_color_dark`` with a single
``theme_tokens`` JSONB column that mirrors Ant Design's ``theme.token``.
This lets us grow the set (success/warning/error/info, borderRadius,
fontFamily, ...) without another migration per field.

Existing values for the two old columns are preserved into
``theme_tokens.light.colorPrimary`` / ``theme_tokens.dark.colorPrimary``
before the columns are dropped.

Revision ID: 20260423_brand_tok
Revises: 20260423_brand_thm
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_brand_tok"
down_revision = "20260423_brand_thm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_branding",
        sa.Column("theme_tokens", sa.dialects.postgresql.JSONB, nullable=True),
    )
    # Migrate any values that were stored under the old columns. jsonb_build_object
    # lets us skip nulls server-side and avoids pulling rows into Python.
    op.execute(
        """
        UPDATE system_branding
        SET theme_tokens = jsonb_strip_nulls(jsonb_build_object(
            'light', CASE
                WHEN primary_color_light IS NOT NULL
                THEN jsonb_build_object('colorPrimary', primary_color_light)
                ELSE NULL
            END,
            'dark', CASE
                WHEN primary_color_dark IS NOT NULL
                THEN jsonb_build_object('colorPrimary', primary_color_dark)
                ELSE NULL
            END
        ))
        WHERE primary_color_light IS NOT NULL OR primary_color_dark IS NOT NULL
        """
    )
    op.drop_column("system_branding", "primary_color_light")
    op.drop_column("system_branding", "primary_color_dark")


def downgrade() -> None:
    op.add_column(
        "system_branding",
        sa.Column("primary_color_light", sa.String(9), nullable=True),
    )
    op.add_column(
        "system_branding",
        sa.Column("primary_color_dark", sa.String(9), nullable=True),
    )
    op.execute(
        """
        UPDATE system_branding
        SET primary_color_light = theme_tokens -> 'light' ->> 'colorPrimary',
            primary_color_dark  = theme_tokens -> 'dark'  ->> 'colorPrimary'
        WHERE theme_tokens IS NOT NULL
        """
    )
    op.drop_column("system_branding", "theme_tokens")
