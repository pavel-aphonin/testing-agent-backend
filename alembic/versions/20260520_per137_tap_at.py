"""PER-137 — tap_at(x,y) and enter_text(text) reference actions.

Some UIs render their controls below the accessibility line — PIN
keypads, canvas-painted buttons, React Native screens that don't set
``accessibilityIdentifier``. The worker's synthetic ``element_id``
(``Button_<x>_<y>``) is meaningless to the LLM there, so even when
vision-mode sees the digit "0", the LLM can't point at it through the
constrained enum.

Two new universal actions close this gap without baking anything
app-specific into the worker:

* ``tap_at`` — takes pixel coordinates from the screenshot and taps
  there directly. The accessibility tree isn't consulted, so this
  works on any UI regardless of its instrumentation.
* ``enter_text`` — types into the active focus (usually the field
  the user / LLM just tapped). Lets the agent fill OS-keyboards and
  custom inputs without needing a per-field set_text_in_field
  call.

Both actions have ``arguments_schema`` so llama-server's
constrained-decoding compiles a grammar that demands the right
fields and types. Element_id stays optional on the worker side for
``tap_at`` (no need to ground the tap on an AXe element).

Revision ID: 20260520_per137
Revises: 20260520_per131
Create Date: 2026-05-20
"""
from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op


revision = "20260520_per137"
down_revision = "20260520_per131"
branch_labels = None
depends_on = None


_NEW_ACTIONS: list[dict] = [
    {
        "code": "tap_at",
        "name": "Тап по координатам",
        "description": (
            "Нажать в точке (x, y) на экране. Используется когда нужная "
            "кнопка видна на скриншоте, но её нет в списке элементов "
            "экрана — например, цифры на PIN-клавиатуре или canvas-кнопки."
        ),
        "platform_scope": "universal",
        "arguments_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer", "minimum": 0, "maximum": 4096},
                "y": {"type": "integer", "minimum": 0, "maximum": 4096},
            },
        },
    },
    {
        "code": "enter_text",
        "name": "Ввести текст в активное поле",
        "description": (
            "Напечатать строку в поле, на котором сейчас находится фокус "
            "(после tap по полю или когда OS-keyboard уже открыта). "
            "Используется когда у поля ввода нет accessibilityIdentifier, "
            "и через input по element_id зайти нельзя."
        ),
        "platform_scope": "universal",
        "arguments_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
        },
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    for action in _NEW_ACTIONS:
        # ``ref_action_types`` uses ``code`` as natural key. ON CONFLICT
        # by code keeps the migration idempotent if it's re-run.
        bind.execute(
            sa.text(
                """
                INSERT INTO ref_action_types
                  (id, code, name, description, platform_scope,
                   arguments_schema, is_active, is_system)
                VALUES
                  (:id, :code, :name, :description, :platform_scope,
                   CAST(:args AS jsonb), true, true)
                ON CONFLICT (code) DO UPDATE
                  SET name = EXCLUDED.name,
                      description = EXCLUDED.description,
                      arguments_schema = EXCLUDED.arguments_schema,
                      is_active = true
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "code": action["code"],
                "name": action["name"],
                "description": action["description"],
                "platform_scope": action["platform_scope"],
                "args": json.dumps(action["arguments_schema"]),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM ref_action_types WHERE code IN ('tap_at', 'enter_text')"
        )
    )
