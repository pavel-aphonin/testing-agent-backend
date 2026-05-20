"""PER-138 — full capabilities adapter for LLMModel.

PER-131-lite added three thinking-related fields. PER-138 extends
the passport so the worker can talk to any model the operator plugs
in — Gemma 4 via llama.cpp, Qwen3 from HuggingFace, OpenAI-compat
API, marketplace plugins like AlphaGen — without code changes.

Five new columns:

* ``provider``                  — backend protocol (llama_cpp / openai_compat /
                                  anthropic / alphagen / …). Defaults to
                                  ``llama_cpp`` since that's the only
                                  codepath the worker actually has today;
                                  others come online as adapter classes
                                  land.
* ``endpoint_url``              — base URL for requests; empty falls back
                                  to the existing ``TA_LLM_BASE_URL`` env.
* ``supports_json_schema``      — true if the backend understands
                                  ``response_format={type: json_schema, ...}``.
                                  False → worker sends without it and
                                  validates the JSON itself.
* ``supports_multimodal_image`` — true if the backend accepts an
                                  image content block. False → worker
                                  drops ``screenshot_b64`` even when
                                  the goal-decide loop has one.
* ``max_context_tokens``        — for history budget. Cosmetic right
                                  now (worker doesn't trim to it yet),
                                  but stored so the UI can show it.

Defaults set by the migration keep current Gemma 4 behaviour
intact: llama_cpp / supports_json_schema=true / supports_multimodal=
the existing ``supports_vision`` flag.

Revision ID: 20260520_per138
Revises: 20260520_per137_prompt
Create Date: 2026-05-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260520_per138"
down_revision = "20260520_per137_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "provider",
            sa.String(50),
            nullable=False,
            server_default="llama_cpp",
        ),
    )
    op.add_column(
        "llm_models",
        sa.Column("endpoint_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column(
            "supports_json_schema",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "llm_models",
        sa.Column(
            "supports_multimodal_image",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "llm_models",
        sa.Column(
            "max_context_tokens",
            sa.Integer(),
            nullable=False,
            server_default="32768",
        ),
    )

    # Backfill: for any existing row whose ``supports_vision`` is
    # already true, mirror it into ``supports_multimodal_image``. The
    # two flags are not synonyms long-term — supports_vision describes
    # the *model*'s capability, supports_multimodal_image describes
    # the *transport* (does the endpoint accept image_url content
    # blocks). For llama_cpp they're the same.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE llm_models "
            "SET supports_multimodal_image = supports_vision"
        )
    )


def downgrade() -> None:
    op.drop_column("llm_models", "max_context_tokens")
    op.drop_column("llm_models", "supports_multimodal_image")
    op.drop_column("llm_models", "supports_json_schema")
    op.drop_column("llm_models", "endpoint_url")
    op.drop_column("llm_models", "provider")
