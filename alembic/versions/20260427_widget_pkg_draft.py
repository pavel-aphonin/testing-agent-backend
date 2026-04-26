"""Draft vs published for widget_packages.

Adds ``draft_html_source`` (nullable). Existing ``html_source`` keeps
its meaning — what's currently published and rendered on dashboards.
Draft is what the author edits without disturbing live widgets;
clicking «Опубликовать» copies draft → html_source and bumps the
patch version.

Revision ID: 20260427_widget_draft
Revises: 20260427_widget_sort
Create Date: 2026-04-27
"""

import sqlalchemy as sa
from alembic import op

revision = "20260427_widget_draft"
down_revision = "20260427_widget_sort"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "widget_packages",
        sa.Column("draft_html_source", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("widget_packages", "draft_html_source")
