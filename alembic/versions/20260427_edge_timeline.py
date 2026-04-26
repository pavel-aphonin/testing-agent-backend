"""Edge timeline fields: before/after screenshot paths + llm_reasoning.

Powers the per-step timeline (PER-25) on /runs/{id}/results. All
three fields are nullable so older edges and edges from non-LLM
modes keep working — the UI hides the screenshot column when the
path is null and falls back to the action description when there's
no LLM reasoning.

Revision ID: 20260427_edge_tl
Revises: 20260427_widget_draft
Create Date: 2026-04-27
"""

import sqlalchemy as sa
from alembic import op

revision = "20260427_edge_tl"
down_revision = "20260427_widget_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edges", sa.Column("screenshot_before_path", sa.Text, nullable=True))
    op.add_column("edges", sa.Column("screenshot_after_path", sa.Text, nullable=True))
    op.add_column("edges", sa.Column("llm_reasoning", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("edges", "llm_reasoning")
    op.drop_column("edges", "screenshot_after_path")
    op.drop_column("edges", "screenshot_before_path")
