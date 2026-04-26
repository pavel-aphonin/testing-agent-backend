"""Diff two runs — what's new, what disappeared, what changed.

The match key for screens and edges is the same hash the rest of the
system uses (``screen_id_hash``), so diffing is a pure set operation
once both rows are loaded.

Defects don't have a stable id across runs (they're generated fresh),
so we match by the natural-key tuple ``(screen_hash, kind, title)``.
That yields three buckets the UI can render with a green / grey /
yellow palette:
    new       — present in current, absent in baseline
    resolved  — present in baseline, absent in current
    persisted — present in both
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.defect import DefectModel
from app.models.run import Edge, Run, Screen


async def _load_run(session: AsyncSession, run_id: UUID) -> Run | None:
    return (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()


async def diff_runs(
    session: AsyncSession,
    current_id: UUID,
    baseline_id: UUID,
) -> dict[str, Any]:
    """Compute the diff payload returned by GET /api/runs/{id}/diff.

    Both runs must exist; the caller is responsible for the access
    check (current run + dashboard / role rules) — this is a pure
    data function.
    """
    cur = await _load_run(session, current_id)
    base = await _load_run(session, baseline_id)
    if cur is None or base is None:
        return {"error": "run_not_found"}

    cur_screens = {
        s.screen_id_hash: {"hash": s.screen_id_hash, "name": s.name}
        for s in (await session.execute(
            select(Screen).where(Screen.run_id == current_id)
        )).scalars().all()
    }
    base_screens = {
        s.screen_id_hash: {"hash": s.screen_id_hash, "name": s.name}
        for s in (await session.execute(
            select(Screen).where(Screen.run_id == baseline_id)
        )).scalars().all()
    }

    cur_edges = {
        (e.source_screen_hash, e.target_screen_hash, e.action_type): {
            "source_hash": e.source_screen_hash,
            "target_hash": e.target_screen_hash,
            "action_type": e.action_type,
            "success": e.success,
        }
        for e in (await session.execute(
            select(Edge).where(Edge.run_id == current_id)
        )).scalars().all()
    }
    base_edges = {
        (e.source_screen_hash, e.target_screen_hash, e.action_type): {
            "source_hash": e.source_screen_hash,
            "target_hash": e.target_screen_hash,
            "action_type": e.action_type,
            "success": e.success,
        }
        for e in (await session.execute(
            select(Edge).where(Edge.run_id == baseline_id)
        )).scalars().all()
    }

    cur_defects = {
        (d.screen_id_hash or "", d.kind or "", d.title or ""): {
            "id": str(d.id),
            "screen_name": d.screen_name,
            "priority": d.priority,
            "kind": d.kind,
            "title": d.title,
        }
        for d in (await session.execute(
            select(DefectModel).where(DefectModel.run_id == current_id)
        )).scalars().all()
    }
    base_defects = {
        (d.screen_id_hash or "", d.kind or "", d.title or ""): {
            "id": str(d.id),
            "priority": d.priority,
            "kind": d.kind,
            "title": d.title,
        }
        for d in (await session.execute(
            select(DefectModel).where(DefectModel.run_id == baseline_id)
        )).scalars().all()
    }

    # Edges that exist in both keys but flipped success state — a
    # regression in the literal sense ("worked, now doesn't"). We
    # surface them separately from added/removed so the UI can show
    # a yellow "regressions" section.
    edges_changed = []
    shared_edge_keys = cur_edges.keys() & base_edges.keys()
    for k in shared_edge_keys:
        if cur_edges[k]["success"] != base_edges[k]["success"]:
            edges_changed.append({
                **cur_edges[k],
                "old_success": base_edges[k]["success"],
                "new_success": cur_edges[k]["success"],
            })

    return {
        "current_run_id": str(current_id),
        "baseline_run_id": str(baseline_id),
        "screens_added": [v for k, v in cur_screens.items() if k not in base_screens],
        "screens_removed": [v for k, v in base_screens.items() if k not in cur_screens],
        "edges_added": [v for k, v in cur_edges.items() if k not in base_edges],
        "edges_removed": [v for k, v in base_edges.items() if k not in cur_edges],
        "edges_changed": edges_changed,
        "defects_new": [v for k, v in cur_defects.items() if k not in base_defects],
        "defects_resolved": [v for k, v in base_defects.items() if k not in cur_defects],
        "defects_persisted": [v for k, v in cur_defects.items() if k in base_defects],
        "summary": {
            "screens_added":     len(cur_screens.keys() - base_screens.keys()),
            "screens_removed":   len(base_screens.keys() - cur_screens.keys()),
            "edges_added":       len(cur_edges.keys() - base_edges.keys()),
            "edges_removed":     len(base_edges.keys() - cur_edges.keys()),
            "edges_changed":     len(edges_changed),
            "defects_new":       len(cur_defects.keys() - base_defects.keys()),
            "defects_resolved":  len(base_defects.keys() - cur_defects.keys()),
            "defects_persisted": len(cur_defects.keys() & base_defects.keys()),
        },
    }
