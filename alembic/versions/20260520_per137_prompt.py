"""PER-137 — goal_decide.system prompt rule E3 about tap_at fallback.

When the LLM can see a button on the screenshot (vision input) but
can't find it in the «Элементы экрана» enum — that's the case
``tap_at(x,y)`` is for. Make it explicit in the system prompt so the
model uses it instead of falling back to ``back`` or random taps.

Inserts a new paragraph **E3** right after **E2** in the existing
prompt — minimal-surface edit, doesn't rewrite anything else.

Revision ID: 20260520_per137_prompt
Revises: 20260520_per137
Create Date: 2026-05-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260520_per137_prompt"
down_revision = "20260520_per137"
branch_labels = None
depends_on = None


_E2_MARKER = """E2. Если нужного элемента не видно на экране — сначала попробуй
    действия навигации/прокрутки (то, что разрешено справочником
    действий), чтобы его открыть, и только потом — целевой ввод /
    нажатие к нему. Не пытайся обратиться к элементу, которого нет
    в списке."""

_E2_PLUS_E3 = _E2_MARKER + """

E3. Если на скриншоте ты видишь нужную кнопку (цифру PIN, иконку,
    custom-элемент), но её нет в списке элементов экрана — используй
    действие `tap_at` с координатами этой кнопки на скриншоте (если
    оно есть в справочнике действий). `element_id` для `tap_at`
    оставь null. Аналогично, если поле ввода уже в фокусе, но через
    `input` к нему не получается обратиться (нет AXUniqueId / лейбла),
    используй `enter_text` с нужным текстом."""


def upgrade() -> None:
    bind = op.get_bind()
    # In-place text replace — keeps any admin edits surrounding E2
    # intact. If the marker isn't found (admin already rewrote that
    # paragraph), the UPDATE is a no-op for the content column but
    # still refreshes default_content with the new shape so a reset
    # delivers the new wording.
    bind.execute(
        sa.text(
            "UPDATE system_prompts "
            "SET content = REPLACE(content, :old, :new), "
            "    default_content = REPLACE(default_content, :old, :new) "
            "WHERE code = 'goal_decide.system'"
        ),
        {"old": _E2_MARKER, "new": _E2_PLUS_E3},
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
        {"old": _E2_MARKER, "new": _E2_PLUS_E3},
    )
