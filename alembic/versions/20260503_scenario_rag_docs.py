"""Scenario rag_document_ids: scope RAG verification to a doc allow-list.

Adds nullable JSONB column. NULL / [] means "use the whole workspace
corpus" (legacy behaviour); a non-empty list restricts the worker's
_check_rag() to chunks belonging only to those documents.

Revision ID: 20260503_scenario_rag
Revises: 20260427_edge_tl
Create Date: 2026-05-03
"""

import sqlalchemy as sa
from alembic import op

revision = "20260503_scenario_rag"
down_revision = "20260427_edge_tl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenarios",
        sa.Column(
            "rag_document_ids",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scenarios", "rag_document_ids")
