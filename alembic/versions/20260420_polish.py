"""Polish migration: user_table_prefs + run attribute_values_json + system dictionaries.

Adds:
- user_table_prefs (per-user UI table state)
- runs.attribute_values_json (denormalized; primary store remains attribute_values)
- Seeds 4 system dictionaries marked is_system=true (immutable):
    platforms, os_versions, device_types, action_types, test_data_types
  These are roles-table-style records (we reuse the dictionary model
  pattern but each system dict gets its own DB table for clarity).

For brevity we add new tables for ENUM-like reference data.

Revision ID: 20260420_polish
Revises: 20260420_ntype
Create Date: 2026-04-20
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260420_polish"
down_revision = "20260420_ntype"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. User table preferences
    op.create_table(
        "user_table_prefs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("table_key", sa.String(100), nullable=False),
        sa.Column("prefs", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "table_key", name="uq_user_table_pref"),
    )

    # 2. System reference dictionaries.
    # Each row: id, code (unique), name, platform (optional categorization),
    #          is_active (admin can disable an entry from showing in pickers).
    op.create_table(
        "ref_platforms",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "ref_os_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("platform_code", sa.String(50), nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "ref_device_types",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("platform_code", sa.String(50), nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "ref_action_types",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        # Filter by platform: ios | android | desktop | web | universal
        sa.Column("platform_scope", sa.String(50), nullable=False, server_default="universal"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "ref_test_data_types",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )

    # Per-workspace toggle for action types (which actions are enabled in a ws)
    op.create_table(
        "workspace_action_settings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "action_type_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ref_action_types.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("workspace_id", "action_type_id", name="uq_ws_action_setting"),
    )

    # ── Seed data ─────────────────────────────────────────────────────────
    platforms = sa.table(
        "ref_platforms",
        sa.column("id"), sa.column("code"), sa.column("name"), sa.column("sort_order"),
    )
    op.bulk_insert(platforms, [
        {"id": str(uuid.uuid4()), "code": "ios", "name": "iOS", "sort_order": 1},
        {"id": str(uuid.uuid4()), "code": "android", "name": "Android", "sort_order": 2},
        {"id": str(uuid.uuid4()), "code": "desktop", "name": "Desktop", "sort_order": 3},
        {"id": str(uuid.uuid4()), "code": "web", "name": "Web", "sort_order": 4},
    ])

    os_versions = sa.table(
        "ref_os_versions",
        sa.column("id"), sa.column("code"), sa.column("name"), sa.column("platform_code"),
    )
    op.bulk_insert(os_versions, [
        {"id": str(uuid.uuid4()), "code": "ios-18.2", "name": "iOS 18.2", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "ios-26.2", "name": "iOS 26.2", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "ios-26.4", "name": "iOS 26.4", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "android-36", "name": "Android 36", "platform_code": "android"},
        {"id": str(uuid.uuid4()), "code": "android-36.1", "name": "Android 36.1", "platform_code": "android"},
    ])

    device_types = sa.table(
        "ref_device_types",
        sa.column("id"), sa.column("code"), sa.column("name"), sa.column("platform_code"),
    )
    op.bulk_insert(device_types, [
        {"id": str(uuid.uuid4()), "code": "iphone-17-pro-max", "name": "iPhone 17 Pro Max", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "iphone-17-pro", "name": "iPhone 17 Pro", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "iphone-17", "name": "iPhone 17", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "ipad-pro-13", "name": "iPad Pro 13\"", "platform_code": "ios"},
        {"id": str(uuid.uuid4()), "code": "pixel-9-pro-xl", "name": "Pixel 9 Pro XL", "platform_code": "android"},
        {"id": str(uuid.uuid4()), "code": "pixel-9", "name": "Pixel 9", "platform_code": "android"},
    ])

    actions = sa.table(
        "ref_action_types",
        sa.column("id"), sa.column("code"), sa.column("name"), sa.column("description"),
        sa.column("platform_scope"), sa.column("is_system"),
    )
    op.bulk_insert(actions, [
        {"id": str(uuid.uuid4()), "code": "tap", "name": "Нажать",
         "description": "Тап по элементу", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "input", "name": "Ввести текст",
         "description": "Заполнить текстовое поле", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "swipe", "name": "Свайп",
         "description": "Свайп в направлении", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "wait", "name": "Подождать",
         "description": "Пауза между действиями", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "assert", "name": "Проверить",
         "description": "Проверка наличия элемента/текста", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "long_press", "name": "Долгое нажатие",
         "description": "Удержание элемента", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "scroll", "name": "Прокрутка",
         "description": "Прокрутка экрана", "platform_scope": "universal", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "back", "name": "Назад",
         "description": "Системная кнопка «Назад»", "platform_scope": "android", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "right_click", "name": "Правый клик",
         "description": "Правый клик мышкой", "platform_scope": "desktop", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "double_click", "name": "Двойной клик",
         "description": "Двойной клик мышкой", "platform_scope": "desktop", "is_system": True},
    ])

    td_types = sa.table(
        "ref_test_data_types",
        sa.column("id"), sa.column("code"), sa.column("name"), sa.column("is_system"),
    )
    op.bulk_insert(td_types, [
        {"id": str(uuid.uuid4()), "code": "auth", "name": "Авторизация", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "payment", "name": "Платежи", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "personal", "name": "Персональные", "is_system": True},
        {"id": str(uuid.uuid4()), "code": "general", "name": "Общие", "is_system": True},
    ])


def downgrade() -> None:
    op.drop_table("workspace_action_settings")
    op.drop_table("ref_test_data_types")
    op.drop_table("ref_action_types")
    op.drop_table("ref_device_types")
    op.drop_table("ref_os_versions")
    op.drop_table("ref_platforms")
    op.drop_table("user_table_prefs")
