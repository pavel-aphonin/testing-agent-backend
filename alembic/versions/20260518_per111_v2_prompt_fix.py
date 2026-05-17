"""PER-111 v2 — prompt fix: drop category-specific field examples

The goal_decide.system prompt seeded by 20260518_per111_v2 had a
parenthetical in rule V3 that listed concrete field categories
("для email — формат email, для телефона — формат телефона, для
названия заметки — короткая фраза"). Even though they were neutral
examples, they leaked specific test_data semantics into the system
prompt — a hardcode aimed straight at the universality requirement.

This migration replaces the V3 paragraph with a category-agnostic
formulation that ties the format choice to what's visible on screen.
The system prompt is overwritten in BOTH `default_content` and
`content`, so admins who never edited the seed get the new text and
admins who did keep their custom edits (they live only in `content`).

Revision ID: 20260518_per111_v2_prompt_fix
Revises: 20260518_per111_v2
Create Date: 2026-05-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260518_per111_v2_prompt_fix"
down_revision = "20260518_per111_v2"
branch_labels = None
depends_on = None


_OLD_V3 = """V3. Если ни цель, ни `test_data` не дают значения, придумай его сам:
    `value_source = "improvised"` и `value_literal` — твоё разумное
    значение. Старайся, чтобы оно выглядело правдоподобно для
    конкретного поля (для email — формат email, для телефона —
    формат телефона, для названия заметки — короткая фраза). Если
    на следующем шаге снова нужно то же поле (тот же `element_id`)
    — верни тот же `value_literal` без изменений."""

_NEW_V3 = """V3. Если ни цель, ни `test_data` не дают значения, придумай его сам:
    `value_source = "improvised"` и `value_literal` — твоё разумное
    значение. Формат значения должен соответствовать семантике поля,
    видной на экране (тип поля, подпись, плейсхолдер): для поля,
    которое явно ждёт e-mail — строка вида адреса; для числового
    поля — число; для свободной строки — короткая правдоподобная
    фраза. Не делай предположений за пределами того, что показывает
    UI. Если на следующем шаге снова нужно то же поле
    (тот же `element_id`) — верни тот же `value_literal` без изменений."""


def upgrade() -> None:
    bind = op.get_bind()
    # In-place text replace: keeps any admin edits surrounding the V3
    # paragraph intact. If the V3 marker isn't found (admin already
    # rewrote it) the UPDATE is a no-op — REPLACE returns the same
    # string and we still write it back, but the result is equivalent.
    bind.execute(
        sa.text(
            "UPDATE system_prompts "
            "SET content = REPLACE(content, :old, :new), "
            "    default_content = REPLACE(default_content, :old, :new) "
            "WHERE code = 'goal_decide.system'"
        ),
        {"old": _OLD_V3, "new": _NEW_V3},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE system_prompts "
            "SET content = REPLACE(content, :new, :old), "
            "    default_content = REPLACE(default_content, :new, :old) "
            "WHERE code = 'goal_decide.system'"
        ),
        {"old": _OLD_V3, "new": _NEW_V3},
    )
