"""Workspaces: isolated project scopes.

Creates ``workspaces`` and ``workspace_members`` tables, adds nullable
``workspace_id`` FK to runs / scenarios / test_data / knowledge_documents,
and seeds the "Модератор" system role.

Revision ID: 20260420_ws
Revises: 20260419_rbac
Create Date: 2026-04-20
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260420_ws"
down_revision = "20260419_rbac"
branch_labels = None
depends_on = None

_MODERATOR_ROLE_ID = str(uuid.uuid4())


def upgrade() -> None:
    # ── 1. Workspaces table ──────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("logo_path", sa.Text, nullable=True),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 2. Workspace members ─────────────────────────────────────────────
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_ws_member"),
    )

    # ── 3. Add workspace_id FK to scoped entities ────────────────────────
    for table in ("runs", "scenarios", "test_data", "knowledge_documents"):
        op.add_column(
            table,
            sa.Column(
                "workspace_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )

    # ── 4. Seed "Модератор" system role ──────────────────────────────────
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("name", sa.String),
        sa.column("code", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
        sa.column("permissions", sa.dialects.postgresql.JSONB),
    )
    op.bulk_insert(roles_table, [
        {
            "id": _MODERATOR_ROLE_ID,
            "code": "moderator",
            "name": "Модератор",
            "description": "Управление участниками рабочих пространств",
            "is_system": True,
            "permissions": [
                "runs.view", "runs.create", "runs.edit", "runs.cancel",
                "scenarios.view", "scenarios.create", "scenarios.edit", "scenarios.delete",
                "test_data.view", "test_data.create", "test_data.edit", "test_data.delete",
                "defects.view",
                "graph.view",
                "knowledge.view", "knowledge.create", "knowledge.edit", "knowledge.delete",
                "devices.view",
                "models.view",
                "settings.view", "settings.edit",
                "assistant.use",
                "dictionaries.view",
            ],
        },
    ])


def downgrade() -> None:
    for table in ("runs", "scenarios", "test_data", "knowledge_documents"):
        op.drop_column(table, "workspace_id")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.execute(f"DELETE FROM roles WHERE id = '{_MODERATOR_ROLE_ID}'")
