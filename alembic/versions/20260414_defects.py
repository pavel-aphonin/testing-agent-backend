"""Add defects table for agent-detected issues with priority ranking.

Revision ID: 20260414_defc
Revises: 20260414_scen
Create Date: 2026-04-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20260414_defc"
down_revision = "20260414_scen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "defects",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_idx", sa.Integer(), nullable=True),
        sa.Column("screen_id_hash", sa.String(length=64), nullable=True),
        sa.Column("screen_name", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.String(length=10), server_default="P2", nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="functional", nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("llm_analysis_json", sa.JSON(), nullable=True),
        sa.Column("external_ticket_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_defects_run_id"), "defects", ["run_id"])
    op.create_index(op.f("ix_defects_priority"), "defects", ["priority"])
    op.create_index(op.f("ix_defects_kind"), "defects", ["kind"])
    op.create_index(op.f("ix_defects_screen_id_hash"), "defects", ["screen_id_hash"])


def downgrade() -> None:
    op.drop_index(op.f("ix_defects_screen_id_hash"), table_name="defects")
    op.drop_index(op.f("ix_defects_kind"), table_name="defects")
    op.drop_index(op.f("ix_defects_priority"), table_name="defects")
    op.drop_index(op.f("ix_defects_run_id"), table_name="defects")
    op.drop_table("defects")
