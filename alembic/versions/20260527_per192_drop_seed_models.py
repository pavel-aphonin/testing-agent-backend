"""PER-192 — deactivate Gemma 4 E4B + Qwen 3.5 35B-A3B seed rows.

PER-175 Phase 1A migration: the two pre-seeded monolithic models
(Gemma 4 E4B, Qwen 3.5 35B-A3B = 27.3 GB VRAM combined) are being
retired in favor of the 12-module pipeline (Planner=GUI-Owl-1.5-4B,
Grounder=UI-TARS-1.5-7B, Safety=Llama-Guard-3-1B, etc.).

Why deactivate (`is_active=false`) instead of hard `DELETE`:

* `llm_models.id` is the FK target for `runs.llm_model_id` and possibly
  other tables. A hard delete would either fail the migration (RESTRICT)
  or silently cascade and nuke historical runs (CASCADE).
* Operators can still see the old rows in the admin catalog for
  reference (and re-activate them in an emergency rollback) — they just
  can't pick them in new run creation, because the picker filters by
  ``is_active=true``.
* The downgrade is therefore symmetric and safe — flips them back to
  active without touching any other table.

Revision ID: 20260527_per192_drop_seed
Revises: 20260525_per172_container
Create Date: 2026-05-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260527_per192_drop_seed"
down_revision = "20260525_per172_container"
branch_labels = None
depends_on = None


# Names match what app/seed.py:INITIAL_MODELS used to insert before
# PER-192 emptied that list. Kept inline rather than importing from
# the (now empty) seed module so the migration is self-contained.
_RETIRED_MODEL_NAMES = ("gemma-4-e4b", "qwen3.5-35b-a3b")


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE llm_models SET is_active = false "
            "WHERE name = ANY(:names)"
        ).bindparams(sa.bindparam("names", value=list(_RETIRED_MODEL_NAMES)))
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE llm_models SET is_active = true "
            "WHERE name = ANY(:names)"
        ).bindparams(sa.bindparam("names", value=list(_RETIRED_MODEL_NAMES)))
    )
