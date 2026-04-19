"""Attributes + attribute_values + seed system attribute (workspace member limit).

Revision ID: 20260420_attr
Revises: 20260420_tree
Create Date: 2026-04-20
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260420_attr"
down_revision = "20260420_tree"
branch_labels = None
depends_on = None


_LIMIT_ATTR_ID = str(uuid.uuid4())


def upgrade() -> None:
    op.create_table(
        "attributes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column("enum_values", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("default_value", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("scope", sa.String(20), nullable=False, server_default="workspace"),
        sa.Column("applies_to", sa.String(50), nullable=False, server_default="workspace"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attributes.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("is_group", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "attribute_values",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attribute_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attributes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column(
            "entity_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("value", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("attribute_id", "entity_type", "entity_id", name="uq_attr_entity"),
    )

    # Seed: workspace member limit (system, undeletable)
    attrs_table = sa.table(
        "attributes",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("data_type", sa.String),
        sa.column("default_value", sa.dialects.postgresql.JSONB),
        sa.column("scope", sa.String),
        sa.column("applies_to", sa.String),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(attrs_table, [
        {
            "id": _LIMIT_ATTR_ID,
            "code": "max_members",
            "name": "Лимит участников",
            "description": "Максимум членов рабочего пространства. 0 или отсутствие = без ограничений.",
            "data_type": "number",
            "default_value": 0,
            "scope": "workspace",
            "applies_to": "workspace",
            "is_system": True,
        },
    ])


def downgrade() -> None:
    op.drop_table("attribute_values")
    op.drop_table("attributes")
