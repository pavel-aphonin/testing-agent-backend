"""Edge.rag_verdict_json — persist per-step RAG verification result.

Worker calls /api/admin/knowledge/query after each navigation step.
Until now the result was logged + fed to defect detector but lost
afterwards. This column lets the UI show ✓/⚠/✗ per timeline step
plus a snippet tooltip without re-running the search.

Revision ID: 20260503_edge_rag
Revises: 20260503_scenario_rag
Create Date: 2026-05-03
"""

import sqlalchemy as sa
from alembic import op

revision = "20260503_edge_rag"
down_revision = "20260503_scenario_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "edges",
        sa.Column("rag_verdict_json", sa.dialects.postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("edges", "rag_verdict_json")
