"""PER-163 retry — screenshot_max_dim on llm_models.

QA pushed back that the previous claim "image_min_tokens makes resize
irrelevant" was wrong. The vision encoder's token budget controls
patch count, but detail lost during PIL resize BEFORE the image
reaches the encoder cannot be recovered. For grounding tasks on
small UI controls (PIN keypad digits, app-icon grids) we want to
send the screenshot closer to its native simulator resolution.

This column makes the resize ceiling per-model. Worker's
``take_screenshot`` reads it and skips downscale when the screenshot
is already within the budget. NULL = legacy behaviour (always
resize to ``self._width × self._height``, which is logical points).

For Qwen3-VL we seed 1920 — high enough to keep keypad detail
(native iPhone 17 Pro Max is 1320×2868, well under 1920×anything),
low enough that the JPEG/PNG base64 doesn't blow the context window.

Revision ID: 20260524_per163_shot_dim
Revises: 20260524_per163_image_tok
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260524_per163_shot_dim"
down_revision = "20260524_per163_image_tok"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("screenshot_max_dim", sa.Integer(), nullable=True),
    )
    # Vision-grounding model gets native-resolution screenshots.
    op.execute(
        "UPDATE llm_models SET screenshot_max_dim = 1920 "
        "WHERE name = 'qwen3-vl-32b-instruct'"
    )


def downgrade() -> None:
    op.drop_column("llm_models", "screenshot_max_dim")
