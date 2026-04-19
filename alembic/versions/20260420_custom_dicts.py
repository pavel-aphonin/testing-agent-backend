"""Custom (per-workspace) dictionaries + attribute source_dictionary_id.

Revision ID: 20260420_cdict
Revises: 20260420_attrx
Create Date: 2026-04-20
"""

import sqlalchemy as sa
from alembic import op

revision = "20260420_cdict"
down_revision = "20260420_attrx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_dictionaries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="linear"),
        sa.Column("is_restricted", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_dictionaries.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("is_group", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "custom_dictionary_items",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dictionary_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_dictionaries.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(100), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_dictionary_items.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("is_group", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Per-user ACL on custom dictionaries (consulted when is_restricted=true)
    op.create_table(
        "custom_dictionary_permissions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dictionary_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_dictionaries.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("can_view", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("can_edit", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dictionary_id", "user_id", name="uq_cdict_perm"),
    )

    # Attributes can reference a custom dictionary as enum source
    op.add_column(
        "attributes",
        sa.Column(
            "source_dictionary_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_dictionaries.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("attributes", "source_dictionary_id")
    op.drop_table("custom_dictionary_permissions")
    op.drop_table("custom_dictionary_items")
    op.drop_table("custom_dictionaries")
