"""App categories → reference dictionary.

Categories used to be hardcoded in the frontend (``integration``,
``automation``, ``visualization``, ``utility``). Moving them to a
table lets admins rename / disable / add / reorder without a deploy.

Revision ID: 20260422_app_cats
Revises: 20260422_apps_perm
Create Date: 2026-04-22
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "20260422_app_cats"
down_revision = "20260422_apps_perm"
branch_labels = None
depends_on = None


_SEED = [
    {"code": "integration",   "name": "Интеграция",   "icon": "🔌", "sort_order": 10},
    {"code": "automation",    "name": "Автоматизация", "icon": "⚡", "sort_order": 20},
    {"code": "visualization", "name": "Визуализация",  "icon": "📊", "sort_order": 30},
    {"code": "utility",       "name": "Утилита",       "icon": "🛠️", "sort_order": 40},
]


def upgrade() -> None:
    op.create_table(
        "ref_app_categories",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        # Seeded rows are system rows — can't be deleted via UI but can
        # be renamed / deactivated / reordered.
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
    )

    table = sa.table(
        "ref_app_categories",
        sa.column("id", PG_UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("icon", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid.uuid4(),
                "code": s["code"],
                "name": s["name"],
                "icon": s["icon"],
                "sort_order": s["sort_order"],
                "is_active": True,
                "is_system": True,
            }
            for s in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_table("ref_app_categories")
