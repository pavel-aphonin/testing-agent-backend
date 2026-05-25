"""PER-164 — add optional target_description to tap_at action args.

When a dedicated UI-grounder (UI-TARS et al) is configured, the
chat-LLM no longer needs to guess pixel coordinates for canvas
controls (PIN keypad digits, app-icon grids). Instead it emits
``tap_at`` with a human-readable description of the target —
"digit 8 on PIN keypad", "Continue button in green at the
bottom" — and the worker routes the actual pixel location to the
grounder before tapping.

We keep ``x`` and ``y`` in the schema as fallback when no grounder
is active (pre-PER-164 behaviour preserved), so the LLM can
provide both or either depending on configuration. The runtime
dispatcher prefers ``target_description`` whenever the grounder
client is wired up.

Revision ID: 20260524_per164_tap_target
Revises: 20260524_per164_grounders
"""
from __future__ import annotations

import json

from alembic import op


revision = "20260524_per164_tap_target"
down_revision = "20260524_per164_grounders"
branch_labels = None
depends_on = None


_NEW_SCHEMA = {
    "type": "object",
    # No fields are required at the JSON-schema level — the runtime
    # validates that at least one of (x+y) or target_description is
    # present and emits a clearer error message than the schema
    # would. Forcing required=[x,y] here would prevent the LLM
    # from picking the description-only path even when a grounder
    # is active.
    "properties": {
        "x": {"type": "integer", "minimum": 0, "maximum": 4096},
        "y": {"type": "integer", "minimum": 0, "maximum": 4096},
        "target_description": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300,
            "description": (
                "Human-readable description of what to tap (e.g. "
                "\"digit 8 on PIN keypad\", \"Continue button at "
                "the bottom\"). When a dedicated UI-grounder is "
                "active, the worker uses this instead of x/y to "
                "localise the target — denser-than-Qwen-VL "
                "grounding models can do this reliably even on "
                "canvas-rendered keypads where pixel-level "
                "estimation by the chat-LLM fails. When no "
                "grounder is active, the description is ignored "
                "and (x, y) is used as before."
            ),
        },
    },
    "additionalProperties": False,
}

_OLD_SCHEMA = {
    "type": "object",
    "required": ["x", "y"],
    "properties": {
        "x": {"type": "integer", "minimum": 0, "maximum": 4096},
        "y": {"type": "integer", "minimum": 0, "maximum": 4096},
    },
    "additionalProperties": False,
}


def upgrade() -> None:
    op.execute(
        "UPDATE ref_action_types SET arguments_schema = "
        f"'{json.dumps(_NEW_SCHEMA)}'::jsonb "
        "WHERE code = 'tap_at'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE ref_action_types SET arguments_schema = "
        f"'{json.dumps(_OLD_SCHEMA)}'::jsonb "
        "WHERE code = 'tap_at'"
    )
