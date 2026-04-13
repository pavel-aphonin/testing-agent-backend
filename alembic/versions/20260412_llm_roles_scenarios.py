"""add LLM role columns to agent_settings + scenarios table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. LLM role assignments in agent_settings
    op.add_column(
        "agent_settings",
        sa.Column(
            "vision_model_id",
            sa.UUID(),
            sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_settings",
        sa.Column(
            "thinking_model_id",
            sa.UUID(),
            sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_settings",
        sa.Column(
            "instruct_model_id",
            sa.UUID(),
            sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_settings",
        sa.Column(
            "coder_model_id",
            sa.UUID(),
            sa.ForeignKey("llm_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_settings",
        sa.Column(
            "rag_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # 2. Scenarios table
    op.create_table(
        "scenarios",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("scenarios")
    op.drop_column("agent_settings", "rag_enabled")
    op.drop_column("agent_settings", "coder_model_id")
    op.drop_column("agent_settings", "instruct_model_id")
    op.drop_column("agent_settings", "thinking_model_id")
    op.drop_column("agent_settings", "vision_model_id")
