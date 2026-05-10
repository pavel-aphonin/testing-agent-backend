"""scenarios.is_active default to False (PER-68)

New scenarios start INACTIVE — explicit user activation required.
Existing rows are not touched — their current state is preserved.

Revision ID: 20260510_scen_act
Revises: 20260503_run_replay
Create Date: 2026-05-10
"""
from __future__ import annotations

from alembic import op


revision = "20260510_scen_act"
down_revision = "20260503_run_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change the column's server_default. Existing data is left as-is —
    # only newly inserted rows that don't specify is_active explicitly
    # will get False instead of True.
    op.alter_column(
        "scenarios",
        "is_active",
        server_default="false",
    )


def downgrade() -> None:
    op.alter_column(
        "scenarios",
        "is_active",
        server_default="true",
    )
