"""PER-120 — defect priorities + severities as reference tables

Replace the hardcoded ``DefectPriority`` StrEnum (P0/P1/P2/P3) and the
implicit single-severity model with two proper reference tables:

* ``ref_defect_priorities`` — how urgent the fix is (Urgent/High/Medium/Low).
* ``ref_defect_severities`` — how bad the bug itself is (Blocker/Critical/…).

Both tables share the same shape (the admin UI renders them with one
component, see PER-120 frontend):

* ``id``           — UUID PK
* ``code``         — short stable string used in API (e.g. "urgent"),
                     unique per table; this is the "alias" the user
                     asked for, ASCII-only so non-localised callers
                     (CSV import, external integrations) work.
* ``name``         — human-readable label, may contain Cyrillic.
* ``color``        — hex like "#ff4d4f", used for the UI chip.
* ``description``  — free-form note shown in the admin reference
                     editor so the user can remember why each level
                     exists.
* ``sort_order``   — integer; drag-and-drop reorders mutate this.
                     Lower number = higher rank.
* ``is_active``    — soft-delete flag; agents pick from active rows.
* ``created_at``   — timestamp.

The ``defects`` table gains FK columns ``priority_id`` and
``severity_id`` to replace the legacy varchar ``priority``. Seed
maps existing P0/P1/P2/P3 strings onto the new priority rows; on a
fresh install that's a no-op.

Revision ID: 20260520_defect_priorities_severities
Revises: 20260518_per111_v2_prompt_fix
Create Date: 2026-05-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision = "20260520_per120"
down_revision = "20260518_per111_v2_prompt_fix"
branch_labels = None
depends_on = None


# Seed data — both lists ship as defaults; admins edit through the
# reference UI. Order is the initial sort_order (top → bottom).
_PRIORITIES = [
    {
        "code": "urgent",
        "name": "Срочный",
        "color": "#ff4d4f",
        "description": (
            "Чинить немедленно. Блокирует выпуск или используется "
            "в активных инцидентах."
        ),
    },
    {
        "code": "high",
        "name": "Высокий",
        "color": "#fa8c16",
        "description": (
            "Берётся в работу в текущем спринте. Заметная боль "
            "для пользователя, но не блокер."
        ),
    },
    {
        "code": "medium",
        "name": "Средний",
        "color": "#faad14",
        "description": (
            "Дефолт. Попадает в очередь по графику команды."
        ),
    },
    {
        "code": "low",
        "name": "Низкий",
        "color": "#52c41a",
        "description": (
            "Несущественно для большинства пользователей. "
            "Берётся при наличии времени."
        ),
    },
]

_SEVERITIES = [
    {
        "code": "blocker",
        "name": "Блокер",
        "color": "#ff4d4f",
        "description": (
            "Падение приложения или невозможность использовать "
            "основной сценарий."
        ),
    },
    {
        "code": "critical",
        "name": "Критическая",
        "color": "#fa541c",
        "description": (
            "Ключевая функция работает неверно или приводит к "
            "потере данных."
        ),
    },
    {
        "code": "major",
        "name": "Значимая",
        "color": "#faad14",
        "description": (
            "Заметное отклонение от спецификации, но обходной "
            "путь существует."
        ),
    },
    {
        "code": "minor",
        "name": "Незначительная",
        "color": "#1890ff",
        "description": (
            "Мелкая UX/UI-проблема, не влияет на функциональность."
        ),
    },
    {
        "code": "trivial",
        "name": "Тривиальная",
        "color": "#8c8c8c",
        "description": (
            "Опечатки, отступы, единичные косметические дефекты."
        ),
    },
]


# Legacy DefectPriority enum string → new priorities.code mapping for
# the data migration. Order-preserving (P0 → urgent, ...).
_LEGACY_PRIORITY_MAP = {
    "P0": "urgent",
    "P1": "high",
    "P2": "medium",
    "P3": "low",
}


def _create_ref_table(table_name: str) -> None:
    """Both reference tables share the same shape — extract it."""
    op.create_table(
        table_name,
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(7), nullable=False, server_default="#8c8c8c"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("code", name=f"uq_{table_name}_code"),
    )


def _seed_ref_table(table_name: str, rows: list[dict]) -> None:
    """Insert seed rows with explicit sort_order based on list order."""
    bind = op.get_bind()
    for i, row in enumerate(rows):
        bind.execute(
            sa.text(
                f"INSERT INTO {table_name} "
                "(code, name, color, description, sort_order) "
                "VALUES (:code, :name, :color, :description, :sort_order)"
            ),
            {**row, "sort_order": i},
        )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create both reference tables with seed data.
    _create_ref_table("ref_defect_priorities")
    _seed_ref_table("ref_defect_priorities", _PRIORITIES)
    _create_ref_table("ref_defect_severities")
    _seed_ref_table("ref_defect_severities", _SEVERITIES)

    # 2. Add FK columns to defects. Nullable for migration step;
    # tightened to NOT NULL after data backfill.
    op.add_column(
        "defects",
        sa.Column(
            "priority_id", PG_UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "defects",
        sa.Column(
            "severity_id", PG_UUID(as_uuid=True), nullable=True
        ),
    )

    # 3. Backfill priority_id from the legacy varchar column. Any row
    # whose old priority isn't in the map (shouldn't happen, but
    # safety) gets the default "medium".
    medium_id = bind.execute(
        sa.text(
            "SELECT id FROM ref_defect_priorities WHERE code = 'medium'"
        )
    ).scalar_one()
    for old_code, new_code in _LEGACY_PRIORITY_MAP.items():
        bind.execute(
            sa.text(
                "UPDATE defects "
                "SET priority_id = (SELECT id FROM ref_defect_priorities "
                "                   WHERE code = :new_code) "
                "WHERE priority = :old_code"
            ),
            {"new_code": new_code, "old_code": old_code},
        )
    bind.execute(
        sa.text(
            "UPDATE defects SET priority_id = :medium "
            "WHERE priority_id IS NULL"
        ),
        {"medium": medium_id},
    )

    # 4. Default every defect to "major" severity until callers
    # start setting it. The agent will fill this in per-defect on
    # PER-129 (RAG-driven detection).
    major_id = bind.execute(
        sa.text(
            "SELECT id FROM ref_defect_severities WHERE code = 'major'"
        )
    ).scalar_one()
    bind.execute(
        sa.text("UPDATE defects SET severity_id = :major"),
        {"major": major_id},
    )

    # 5. Lock both FKs to NOT NULL + add the actual foreign-key
    # constraints. Done after backfill so no row violates them.
    op.alter_column("defects", "priority_id", nullable=False)
    op.alter_column("defects", "severity_id", nullable=False)
    op.create_foreign_key(
        "fk_defects_priority_id",
        "defects",
        "ref_defect_priorities",
        ["priority_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_defects_severity_id",
        "defects",
        "ref_defect_severities",
        ["severity_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 6. Drop the legacy varchar priority column. The data is in
    # priority_id now; keeping the string would just give callers
    # two sources of truth.
    op.drop_index("ix_defects_priority", table_name="defects")
    op.drop_column("defects", "priority")


def downgrade() -> None:
    # Restore legacy varchar priority and copy the codes back.
    op.add_column(
        "defects",
        sa.Column("priority", sa.String(10), nullable=True),
    )
    bind = op.get_bind()
    for old_code, new_code in _LEGACY_PRIORITY_MAP.items():
        bind.execute(
            sa.text(
                "UPDATE defects SET priority = :old_code "
                "WHERE priority_id = "
                "(SELECT id FROM ref_defect_priorities WHERE code = :new_code)"
            ),
            {"new_code": new_code, "old_code": old_code},
        )
    bind.execute(
        sa.text("UPDATE defects SET priority = 'P2' WHERE priority IS NULL")
    )
    op.alter_column("defects", "priority", nullable=False)
    op.create_index("ix_defects_priority", "defects", ["priority"])

    op.drop_constraint("fk_defects_priority_id", "defects", type_="foreignkey")
    op.drop_constraint("fk_defects_severity_id", "defects", type_="foreignkey")
    op.drop_column("defects", "priority_id")
    op.drop_column("defects", "severity_id")
    op.drop_table("ref_defect_severities")
    op.drop_table("ref_defect_priorities")
