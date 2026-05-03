"""Run.replay_of + replay_actions_json + started_from_screen_hash.

Underpins PER-40 (replay path API) and PER-41 (start-from-screen):
both endpoints create a new run that carries a recorded action
sequence the worker plays back before free exploration. ``replay_of``
links back to the source run so the UI can render a "this is a
replay of run X" banner.

Revision ID: 20260503_run_replay
Revises: 20260503_edge_rag
Create Date: 2026-05-03
"""

import sqlalchemy as sa
from alembic import op

revision = "20260503_run_replay"
down_revision = "20260503_edge_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "replay_of",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column("replay_actions_json", sa.dialects.postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("started_from_screen_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_runs_replay_of", "runs", ["replay_of"])


def downgrade() -> None:
    op.drop_index("ix_runs_replay_of", table_name="runs")
    op.drop_column("runs", "started_from_screen_hash")
    op.drop_column("runs", "replay_actions_json")
    op.drop_column("runs", "replay_of")
