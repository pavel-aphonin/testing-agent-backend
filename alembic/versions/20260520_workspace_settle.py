"""PER-127 — workspace screen-stability settings.

Adds three columns to ``workspaces`` so the runner's
``_wait_for_screen_stable`` can be tuned per workspace without a
redeploy:

* ``settle_timeout_ms``     — max time to wait for the screen's
                              accessibility tree to stop changing
                              (default 5000).
* ``settle_poll_ms``        — how often to re-snapshot the tree
                              during that wait (default 500).
* ``loading_indicator_keywords`` — JSONB list of substrings the
                              worker scans every element's label/
                              value for; when any match, the
                              screen is "still loading" regardless
                              of fingerprint convergence.

Defaults below are language-agnostic enough to work out of the box
for both Russian and English UIs; admins can edit them per
workspace through Настройки.

Revision ID: 20260520_per127
Revises: 20260520_per120
Create Date: 2026-05-20
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "20260520_per127"
down_revision = "20260520_per120"
branch_labels = None
depends_on = None


# Keywords are matched case-insensitively against AXe ``label`` and
# ``value`` text. Cover both Russian and English defaults plus a
# couple of generic shapes ("ждите", "wait") so the seed doesn't
# bias toward any one app. Admins extend this per workspace.
_DEFAULT_LOADING_KEYWORDS = [
    "loading",
    "please wait",
    "wait",
    "загруз",       # «загружаем», «загрузка», «загружается»
    "секундоч",     # «секундочку, пожалуйста»
    "подожд",       # «подождите», «пожалуйста, подождите»
    "ждите",
]


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "settle_timeout_ms",
            sa.Integer(),
            nullable=False,
            server_default="5000",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "settle_poll_ms",
            sa.Integer(),
            nullable=False,
            server_default="500",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "loading_indicator_keywords",
            JSONB(),
            nullable=False,
            server_default=sa.text(f"'{json.dumps(_DEFAULT_LOADING_KEYWORDS)}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "loading_indicator_keywords")
    op.drop_column("workspaces", "settle_poll_ms")
    op.drop_column("workspaces", "settle_timeout_ms")
