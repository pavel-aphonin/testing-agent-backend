"""PER-162 — runs.baseline_udid for clone-from-baseline simulator runs.

Adds a nullable UDID to ``runs``. When set, the worker clones the
baseline simulator (which the operator pre-configured manually:
logged in, dismissed onboarding, set permissions) instead of
creating a fresh sim and running the full login-flow every time.

Two benefits:
* Run starts in the authorised zone — scenarios that test post-login
  features (transfers, history, profile) actually reach them.
* No more anti-fraud ban after ~10 failed login attempts — the
  backend has seen this device, the session is already trusted.

Followup (separate ticket) will add a full ``device_baselines``
table with metadata (name, description, last_validated_at) and a
UI to register/list/refresh them. For now the operator pastes the
UDID directly into ``runs.baseline_udid`` when creating a run.

Revision ID: 20260523_per162_baseline
Revises: 20260523_per160_input_kb
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260523_per162_baseline"
down_revision = "20260523_per160_input_kb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("baseline_udid", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "baseline_udid")
