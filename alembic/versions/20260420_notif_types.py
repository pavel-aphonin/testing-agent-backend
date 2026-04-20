"""Notification types dictionary + per-workspace settings.

Seeds 4 system types: workspace_invite, run_completed, defect_found, system.

Revision ID: 20260420_ntype
Revises: 20260420_cdict
Create Date: 2026-04-20
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260420_ntype"
down_revision = "20260420_cdict"
branch_labels = None
depends_on = None


_INVITE_ID = str(uuid.uuid4())
_RUN_DONE_ID = str(uuid.uuid4())
_DEFECT_ID = str(uuid.uuid4())
_SYSTEM_ID = str(uuid.uuid4())


def upgrade() -> None:
    op.create_table(
        "notification_types",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("color", sa.String(20), nullable=False, server_default="#888888"),
        sa.Column("icon", sa.String(50), nullable=False, server_default="Bell"),
        sa.Column("template", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "parent_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_types.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("is_group", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "workspace_notification_settings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "notification_type_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_types.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "notification_type_id", name="uq_ws_notif_setting"),
    )

    types_table = sa.table(
        "notification_types",
        sa.column("id", sa.dialects.postgresql.UUID),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("color", sa.String),
        sa.column("icon", sa.String),
        sa.column("template", sa.Text),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(types_table, [
        {
            "id": _INVITE_ID,
            "code": "workspace_invite",
            "name": "Приглашение в пространство",
            "description": "Кто-то приглашает пользователя в рабочее пространство.",
            "color": "#1677ff",
            "icon": "Mail",
            "template": "Приглашение в «{workspace_name}»",
            "is_system": True,
        },
        {
            "id": _RUN_DONE_ID,
            "code": "run_completed",
            "name": "Запуск завершён",
            "description": "Исследование закончилось — успешно или с ошибкой.",
            "color": "#52c41a",
            "icon": "CheckCircle",
            "template": "Запуск {run_title} завершён",
            "is_system": True,
        },
        {
            "id": _DEFECT_ID,
            "code": "defect_found",
            "name": "Найден дефект",
            "description": "Агент обнаружил дефект P0 или P1.",
            "color": "#cf1322",
            "icon": "Bug",
            "template": "{priority}: {defect_title}",
            "is_system": True,
        },
        {
            "id": _SYSTEM_ID,
            "code": "system",
            "name": "Системное сообщение",
            "description": "Прочее системное оповещение.",
            "color": "#888888",
            "icon": "Bell",
            "template": None,
            "is_system": True,
        },
    ])


def downgrade() -> None:
    op.drop_table("workspace_notification_settings")
    op.drop_table("notification_types")
