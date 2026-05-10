"""scenarios: convert flat steps_json to graph v2 (PER-80)

Adds a ``legacy_steps_json`` column to keep the original v1 shape
around (for rollback / debugging) and rewrites every row's
``steps_json`` into the new graph format::

    {
      "version": 2,
      "nodes": [{id, type, position, data}, ...],
      "edges": [{id, source, target, data}, ...]
    }

Linear v1 lists are converted into a straight chain
``start → s0 → s1 → … → end``. Empty / NULL ``steps_json`` is replaced
by a minimal graph with just start + end.

Revision ID: 20260510_scen_graph
Revises: 20260510_scen_act
Create Date: 2026-05-10
"""
from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260510_scen_graph"
down_revision = "20260510_scen_act"
branch_labels = None
depends_on = None


def _v1_to_v2(steps_json: dict[str, Any] | None) -> dict[str, Any]:
    """Inline copy of ``app.schemas.scenario_graph.normalize`` so the
    migration doesn't depend on application code being importable.
    """
    raw: dict[str, Any] = steps_json or {}

    # Already v2 — leave as-is (with version stamped).
    if isinstance(raw.get("nodes"), list) and isinstance(raw.get("edges"), list):
        out = dict(raw)
        out["version"] = 2
        return out

    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    nodes: list[dict[str, Any]] = [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
    ]
    edges: list[dict[str, Any]] = []
    prev = "start"
    for idx, step in enumerate(steps):
        node_id = f"n{idx}"
        nodes.append(
            {
                "id": node_id,
                "type": "action",
                "position": {"x": 0, "y": (idx + 1) * 120},
                "data": dict(step) if isinstance(step, dict) else {},
            }
        )
        edges.append(
            {
                "id": f"e_{prev}_{node_id}",
                "source": prev,
                "target": node_id,
                "data": {},
            }
        )
        prev = node_id

    nodes.append(
        {
            "id": "end",
            "type": "end",
            "position": {"x": 0, "y": (len(steps) + 1) * 120},
            "data": {},
        }
    )
    edges.append(
        {
            "id": f"e_{prev}_end",
            "source": prev,
            "target": "end",
            "data": {},
        }
    )

    return {"version": 2, "nodes": nodes, "edges": edges}


def upgrade() -> None:
    # 1. Stash the v1 shape so we can roll back without losing data.
    op.add_column(
        "scenarios",
        sa.Column(
            "legacy_steps_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 2. Convert every existing row in place.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, steps_json FROM scenarios")
    ).fetchall()

    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE scenarios "
                "SET legacy_steps_json = CAST(:legacy AS jsonb), "
                "    steps_json = CAST(:converted AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "id": row.id,
                "legacy": json.dumps(row.steps_json),
                "converted": json.dumps(_v1_to_v2(row.steps_json)),
            },
        )


def downgrade() -> None:
    # Restore steps_json from legacy_steps_json where present, then drop
    # the legacy column.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE scenarios "
            "SET steps_json = legacy_steps_json "
            "WHERE legacy_steps_json IS NOT NULL"
        )
    )
    op.drop_column("scenarios", "legacy_steps_json")
