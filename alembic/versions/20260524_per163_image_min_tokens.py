"""PER-163 — image_min_tokens on llm_models for VLM grounding.

Vision LLMs need a minimum number of image tokens to do reliable
pixel-level grounding (tap on a specific keypad digit, locate a
small icon, etc.). Qwen3-VL explicitly logs:

    Qwen-VL models require at minimum 1024 image tokens to function
    correctly on grounding tasks. If you encounter problems with
    accuracy, try adding --image-min-tokens 1024

Without the flag llama-server picks an image-token budget based on
the screenshot pixel count and Qwen-VL's default downsampling rules;
on our 440x956-points screenshots that comes out around 420 tokens
— well below the threshold. The audit on PIN-keypad grounding (PER-
163) traced repeated tap_at-same-coords to exactly this: model sees
the PIN keypad as a blurry square and can't distinguish digit 8
from digit 5.

This column lets each model row declare its own minimum. The host
service launcher passes it as ``--image-min-tokens <N>`` when it
boots the chat llama-server. NULL = don't pass the flag (text-only
models, models that need no override).

Revision ID: 20260524_per163_image_tok
Revises: 20260523_per162_baseline
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260524_per163_image_tok"
down_revision = "20260523_per162_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("image_min_tokens", sa.Integer(), nullable=True),
    )
    # Seed Qwen3-VL 32B with the documented minimum so the launcher
    # picks it up immediately on next boot — no manual UPDATE needed.
    op.execute(
        "UPDATE llm_models SET image_min_tokens = 1024 "
        "WHERE name = 'qwen3-vl-32b-instruct'"
    )


def downgrade() -> None:
    op.drop_column("llm_models", "image_min_tokens")
