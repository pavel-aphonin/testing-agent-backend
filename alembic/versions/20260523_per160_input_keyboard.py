"""PER-160 — input action gets bypass_keyboard opt-in arg.

The default ``input`` dispatch now opens the on-screen keyboard and
sends HID keystrokes — same path a real user takes. The previous
``set_text_in_field`` fast path (CDP or AXe set-text injection) is
kept as a strict opt-in via ``action_args.bypass_keyboard=true`` for
the rare cases where the keyboard cannot physically appear (custom
IME, canvas-rendered field, headless flow).

This migration adds ``bypass_keyboard`` (boolean, default false) to
the JSON schema the LLM is constrained against, so the model can
explicitly request the bypass when it has a reason to.

Revision ID: 20260523_per160_input_kb
Revises: 20260522_tap_at_coord
"""
from __future__ import annotations

import json

from alembic import op


revision = "20260523_per160_input_kb"
down_revision = "20260522_tap_at_coord"
branch_labels = None
depends_on = None


_NEW_INPUT_SCHEMA = {
    "type": "object",
    "required": [],
    "properties": {
        "bypass_keyboard": {
            "type": "boolean",
            "description": (
                "True = write the value directly via accessibility/CDP "
                "without opening the on-screen keyboard. Use ONLY when "
                "the keyboard cannot be shown (custom IME, canvas-rendered "
                "field). Default false: agent taps the field, waits for "
                "keyboard, types via HID like a real user."
            ),
            "default": False,
        }
    },
    "additionalProperties": False,
}

_OLD_INPUT_SCHEMA = {
    "type": "object",
    "required": [],
    "properties": {},
    "additionalProperties": False,
}


def upgrade() -> None:
    op.execute(
        f"UPDATE ref_action_types "
        f"SET arguments_schema = '{json.dumps(_NEW_INPUT_SCHEMA)}'::jsonb "
        f"WHERE code = 'input'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE ref_action_types "
        f"SET arguments_schema = '{json.dumps(_OLD_INPUT_SCHEMA)}'::jsonb "
        f"WHERE code = 'input'"
    )
