"""PER-131-lite — capabilities-driven thinking on LLMModel.

The worker should NOT hardcode thinking-mode behavior under one
model. The agent is supposed to work with any model the operator
plugs in — Gemma 4 today, Nemotron / Qwen3-Thinking / a custom HF
checkpoint tomorrow — without code changes.

Three columns on ``llm_models`` describe the model's "thinking
passport":

* ``supports_thinking``      — turn the two-mode handling on/off
                               at all.
* ``thinking_activation``    — text to inject at the start of the
                               system prompt so the model enters
                               thinking mode. Gemma 4 uses
                               ``<|think|>``; Qwen3-Thinking uses a
                               different scheme; non-thinking
                               models leave this blank.
* ``thinking_extract_regex`` — extracts the model's final answer
                               from a thinking-mode response (the
                               model writes its chain-of-thought
                               first, then the answer; the worker
                               needs to know what's the answer).

The migration only ADDS columns. Existing rows get sane defaults
(supports_thinking=false). The currently-seeded Gemma 4 row gets
its passport filled in so the next worker poll picks up thinking
automatically.

Revision ID: 20260520_per131
Revises: 20260520_per127
Create Date: 2026-05-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260520_per131"
down_revision = "20260520_per127"
branch_labels = None
depends_on = None


# Gemma 4 thinking format (from the official model card):
#
#   Activation: include ``<|think|>`` at the start of the system prompt.
#   Output shape:
#       <|channel>thought
#       [internal reasoning]
#       <channel|>
#       [final answer]
#
# The extract regex captures everything after ``<channel|>`` (with an
# optional trailing newline) — that's the part the worker should
# treat as the model's actual answer. DOTALL is implied at the call
# site (``re.search(..., re.DOTALL)``) so the pattern itself doesn't
# need ``(?s)``.
_GEMMA4_THINKING_ACTIVATION = "<|think|>"
_GEMMA4_THINKING_EXTRACT = r"<channel\|>\n?(.*)"


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "supports_thinking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "llm_models",
        sa.Column("thinking_activation", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("thinking_extract_regex", sa.Text(), nullable=True),
    )

    # Seed the passport for any Gemma-4 row we already have. ``family``
    # is the canonical model-family discriminator on llm_models, so any
    # checkpoint flavor (E2B, E4B, 26B-A4B, 31B) gets the same activation
    # syntax — Google ships them as a single protocol.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE llm_models "
            "SET supports_thinking = true, "
            "    thinking_activation = :activation, "
            "    thinking_extract_regex = :extract "
            "WHERE family = :family"
        ),
        {
            "activation": _GEMMA4_THINKING_ACTIVATION,
            "extract": _GEMMA4_THINKING_EXTRACT,
            "family": "gemma-4",
        },
    )


def downgrade() -> None:
    op.drop_column("llm_models", "thinking_extract_regex")
    op.drop_column("llm_models", "thinking_activation")
    op.drop_column("llm_models", "supports_thinking")
