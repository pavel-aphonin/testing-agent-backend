"""PER-145 L1 — per-model coordinate space for tap_at.

Vision-LLMs disagree on what coordinate space they output for
pixel-level grounding tasks:

* **Gemma 3 / Gemma 4**: emit raw screen points (0–440 × 0–956 on
  iPhone 17 Pro Max). Worker can pass through as-is.
* **Qwen2.5-VL / Qwen3-VL**: trained with normalized 0–1000 × 0–1000
  coordinates per the ``{"point_2d": [x, y]}`` convention. Worker
  must scale by actual screen dimensions before calling AXe.
* **Nemotron Reasoning**: emits raw pixels (1320×2868 on iPhone 17
  Pro Max) — same scale-by-3 problem in reverse. Worker can scale
  down if the model is flagged.

This migration adds ``tap_at_coord_space`` to ``llm_models`` so the
worker knows which conversion to apply. Default ``'points'`` keeps
the existing behaviour for Gemma family unchanged.

Allowed values (worker checks):

* ``points``           — pass through, AXe gets raw coords.
* ``normalized_1000``  — scale by (width_pts/1000, height_pts/1000).
* ``pixels``           — scale down by device scale factor (~3x on
                         retina iOS).

Revision ID: 20260522_tap_at_coord
Revises: 20260520_per138
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260522_tap_at_coord"
down_revision = "20260520_per138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column(
            "tap_at_coord_space",
            sa.String(20),
            nullable=False,
            server_default="points",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_models", "tap_at_coord_space")
