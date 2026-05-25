"""PER-164 followup — sampling profile (top_k, min_p) on llm_models.

The worker was hardcoding ``temperature=0.2`` for every chat call and
ignoring the existing ``default_temperature`` / ``default_top_p``
columns. This regression hurt the most for Gemma 4: Google + Unsloth
explicitly recommend ``temperature 0.6-0.7, top_p 0.95, top_k 64,
min_p 0.05`` for agent / structured-output tasks. At ``T=0.2`` Gemma 4
falls into a documented "low-T attractor" (google-deepmind/gemma#647)
that produces overly decisive single-step outputs — empirically the
"wait once → tap back" impatience we saw on loading screens in
PER-164 smoke #5.

Two new columns to complete the sampling passport:

* ``default_top_k`` — limits the candidate token set per step.
  ``NULL`` lets llama-server pick its default (40). Gemma 4 wants 64;
  Qwen-VL family typically wants 20-40.
* ``default_min_p`` — min-p sampling threshold. ``NULL`` disables
  it. Gemma 4 docs recommend 0.05 as a repetition-collapse safety
  floor under JSON-schema constrained decoding (vllm#40080).

Existing columns ``default_temperature`` and ``default_top_p`` keep
their meaning; the worker will start *using* them after this lands.
For Gemma 4 we also fix the seed values to match the recommended
band — old (0.2 / 0.95) → new (0.65 / 0.95 / top_k 64 / min_p 0.05).

Penalty knobs (``repeat_penalty``, ``presence_penalty``,
``frequency_penalty``) are NOT added: research found they interact
badly with ``response_format: json_schema`` (corrupt valid JSON
without curing repetition — see vllm#40080, ollama#15502). They
stay at server default 1.0 / 0.0.

Revision ID: 20260524_per164_sampling
Revises: 20260524_per164_tap_target
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260524_per164_sampling"
down_revision = "20260524_per164_tap_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("default_top_k", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("default_min_p", sa.Float(), nullable=True),
    )
    # Gemma 4 26B A4B — adopt Google/Unsloth recommended sampling.
    # Previous T=0.2 was the root cause of impulsive single-step
    # decisions on loading screens (PER-164 smoke #5).
    op.execute(
        """
        UPDATE llm_models
           SET default_temperature = 0.65,
               default_top_p       = 0.95,
               default_top_k       = 64,
               default_min_p       = 0.05
         WHERE name = 'gemma-4-26b-a4b-it'
        """
    )
    # Qwen 3.6 — Alibaba recommends T=0.7 for non-thinking agent mode,
    # top_p 0.8. Bump from the worker's hardcoded 0.2 to the official
    # band. top_k 20 is Qwen-family convention.
    op.execute(
        """
        UPDATE llm_models
           SET default_temperature = 0.7,
               default_top_p       = 0.8,
               default_top_k       = 20,
               default_min_p       = 0.0
         WHERE name = 'qwen3.6-27b'
        """
    )
    # Qwen3-VL-32B — same Qwen family conventions.
    op.execute(
        """
        UPDATE llm_models
           SET default_temperature = 0.7,
               default_top_p       = 0.8,
               default_top_k       = 20,
               default_min_p       = 0.0
         WHERE name = 'qwen3-vl-32b-instruct'
        """
    )


def downgrade() -> None:
    op.drop_column("llm_models", "default_min_p")
    op.drop_column("llm_models", "default_top_k")
