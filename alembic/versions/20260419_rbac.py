"""Flexible RBAC: roles table + users.role_id FK.

Creates the ``roles`` table, seeds the three system roles (viewer /
tester / admin) with CRUD-style permissions per section, adds
``role_id`` FK to ``users``, and backfills it from the legacy ``role``
string column.

The old ``role`` column is kept for backward compatibility but the
permission system now reads from the ``roles`` relationship.

Revision ID: 20260419_rbac
Revises: 20260415_cleansn
Create Date: 2026-04-19
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260419_rbac"
down_revision = "20260415_cleansn"
branch_labels = None
depends_on = None


# ── System role definitions (must match app/permissions.py) ──────────────────

_VIEWER_ID = str(uuid.uuid4())
_TESTER_ID = str(uuid.uuid4())
_ADMIN_ID = str(uuid.uuid4())

_SYSTEM_ROLES = [
    {
        "id": _VIEWER_ID,
        "code": "viewer",
        "name": "Наблюдатель",
        "description": "Только просмотр запусков и результатов",
        "is_system": True,
        "permissions": [
            "runs.view", "defects.view", "graph.view", "settings.view",
        ],
    },
    {
        "id": _TESTER_ID,
        "code": "tester",
        "name": "Тестировщик",
        "description": "Создание и запуск тестов, управление сценариями и данными",
        "is_system": True,
        "permissions": [
            "runs.view", "runs.create", "runs.edit", "runs.cancel",
            "scenarios.view", "scenarios.create", "scenarios.edit", "scenarios.delete",
            "test_data.view", "test_data.create", "test_data.edit", "test_data.delete",
            "defects.view", "graph.view", "knowledge.view",
            "devices.view", "models.view",
            "settings.view", "settings.edit",
            "assistant.use",
        ],
    },
    {
        "id": _ADMIN_ID,
        "code": "admin",
        "name": "Администратор",
        "description": "Полный доступ ко всем разделам и настройкам",
        "is_system": True,
        "permissions": [
            "assistant.use",
            "defects.create", "defects.delete", "defects.edit", "defects.view",
            "devices.create", "devices.delete", "devices.edit", "devices.view",
            "dictionaries.create", "dictionaries.delete", "dictionaries.edit", "dictionaries.view",
            "graph.view",
            "knowledge.create", "knowledge.delete", "knowledge.edit", "knowledge.reembed", "knowledge.view",
            "models.create", "models.delete", "models.download", "models.edit", "models.view",
            "runs.cancel", "runs.create", "runs.delete", "runs.edit", "runs.view",
            "scenarios.create", "scenarios.delete", "scenarios.edit", "scenarios.view",
            "settings.edit", "settings.view",
            "test_data.create", "test_data.delete", "test_data.edit", "test_data.view",
            "users.create", "users.delete", "users.edit", "users.view",
        ],
    },
]

_CODE_TO_ID = {
    "viewer": _VIEWER_ID,
    "tester": _TESTER_ID,
    "admin": _ADMIN_ID,
}


def upgrade() -> None:
    # 1. Create roles table
    op.create_table(
        "roles",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("permissions", sa.dialects.postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Seed system roles
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("name", sa.String),
        sa.column("code", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
        sa.column("permissions", sa.dialects.postgresql.JSONB),
    )
    op.bulk_insert(roles_table, _SYSTEM_ROLES)

    # 3. Add role_id FK column to users (nullable so existing rows don't break)
    op.add_column(
        "users",
        sa.Column(
            "role_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )

    # 4. Backfill role_id from the legacy role string
    for code, role_id in _CODE_TO_ID.items():
        op.execute(
            f"UPDATE users SET role_id = '{role_id}' WHERE role = '{code}'"
        )

    # 5. Any users with unknown role strings get tester by default
    op.execute(
        f"UPDATE users SET role_id = '{_TESTER_ID}' WHERE role_id IS NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "role_id")
    op.drop_table("roles")
