"""Release notes + per-user dismissal state.

``release_notes`` stores the product changelog — one row per version,
markdown body, shown in the sidebar's «Что нового?» modal and on a
permalink page.

``release_note_dismissals`` records which user has clicked the X on
which note. Absence of a row == unread. Keeping the dismissed list
server-side means the "new" badge survives session wipes and device
switches.

Revision ID: 20260423_release
Revises: 20260423_user_thm
Create Date: 2026-04-23
"""

import sqlalchemy as sa
from alembic import op

revision = "20260423_release"
down_revision = "20260423_user_thm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_notes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(30), nullable=False, unique=True, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text, nullable=True),
        sa.Column("body_md", sa.Text, nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("is_published", sa.Boolean, server_default="true", nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "release_note_dismissals",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "note_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("release_notes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "dismissed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "note_id", name="uq_rn_dismissal"),
    )


def downgrade() -> None:
    op.drop_table("release_note_dismissals")
    op.drop_table("release_notes")
