"""BFS path-finder over a run's edge graph.

Used by:
- PER-40 (replay path API) — to validate user-supplied edge_ids form
  a valid chain.
- PER-41 (start-from-screen) — to find a route from the run's root
  screen to a chosen target.

Kept independent of any HTTP layer; takes raw edge / screen rows in,
returns either a list of edges (the path) or None.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Edge, Screen


async def _load_graph(
    session: AsyncSession, run_id: UUID
) -> tuple[list[Edge], dict[str, str]]:
    """Return (edges, screen_hash → screen_name) for one run."""
    edges = list(
        (await session.execute(
            select(Edge).where(Edge.run_id == run_id)
        )).scalars().all()
    )
    screens = (await session.execute(
        select(Screen.screen_id_hash, Screen.name).where(Screen.run_id == run_id)
    )).all()
    name_by_hash = {row[0]: row[1] for row in screens}
    return edges, name_by_hash


def _root_hash(edges: list[Edge]) -> str | None:
    """Heuristic: the root screen is the one no edge points to.
    With multiple candidates, pick the one that appears as a source
    earliest in the run (lowest min step_idx). Returns None for empty
    runs."""
    if not edges:
        return None
    targets = {e.target_screen_hash for e in edges}
    sources = {e.source_screen_hash for e in edges}
    candidates = sources - targets
    if not candidates:
        # Cyclic graph (every screen has an inbound edge) — fall back
        # to whichever screen the very first edge starts from.
        return min(edges, key=lambda e: e.step_idx).source_screen_hash
    by_first_step: dict[str, int] = {}
    for e in edges:
        if e.source_screen_hash in candidates:
            by_first_step.setdefault(e.source_screen_hash, e.step_idx)
            if e.step_idx < by_first_step[e.source_screen_hash]:
                by_first_step[e.source_screen_hash] = e.step_idx
    return min(candidates, key=lambda h: by_first_step.get(h, 1 << 30))


async def find_path_to(
    session: AsyncSession,
    run_id: UUID,
    target_hash: str,
) -> list[Edge] | None:
    """BFS shortest-path from the run's root screen to ``target_hash``.
    Returns the list of edges traversed (in order) or None if no path
    exists."""
    edges, _ = await _load_graph(session, run_id)
    if not edges:
        return None
    root = _root_hash(edges)
    if root is None:
        return None
    if root == target_hash:
        return []  # already there

    # Adjacency list keyed by source. Within one source we keep the
    # FIRST edge to each target — replaying the earliest wins because
    # later attempts may have failed; the first one was discovered
    # because it actually navigated.
    adj: dict[str, list[Edge]] = {}
    for e in sorted(edges, key=lambda x: x.step_idx):
        adj.setdefault(e.source_screen_hash, []).append(e)

    queue: deque[tuple[str, list[Edge]]] = deque([(root, [])])
    seen: set[str] = {root}
    while queue:
        current, path = queue.popleft()
        for e in adj.get(current, []):
            tgt = e.target_screen_hash
            if tgt in seen:
                continue
            new_path = path + [e]
            if tgt == target_hash:
                return new_path
            seen.add(tgt)
            queue.append((tgt, new_path))
    return None


async def edges_by_ids(
    session: AsyncSession, run_id: UUID, edge_ids: list[int]
) -> list[Edge] | None:
    """Load edges by id, validate they all belong to ``run_id`` AND
    form a connected chain (each edge's target == next edge's source).
    Returns the ordered list or None if validation fails."""
    if not edge_ids:
        return []
    rows = (await session.execute(
        select(Edge).where(Edge.id.in_(edge_ids), Edge.run_id == run_id)
    )).scalars().all()
    by_id = {e.id: e for e in rows}
    if len(by_id) != len(edge_ids):
        return None
    ordered = [by_id[i] for i in edge_ids]
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.target_screen_hash != nxt.source_screen_hash:
            return None
    return ordered


def serialize_action(edge: Edge) -> dict[str, Any]:
    """Convert an Edge into the payload shape the worker can rebuild
    into ActionDetail. Frame is dropped (not stored on Edge); worker
    falls back to tap_by_label which works for everything except
    coordinate-only taps.
    """
    details = (edge.action_details_json or {}) if isinstance(edge.action_details_json, dict) else {}
    return {
        "action_type": edge.action_type,
        "target_label": details.get("element"),
        "input_text": details.get("value"),
        # source/target hashes for richer worker logging on each step.
        "source_screen_hash": edge.source_screen_hash,
        "target_screen_hash": edge.target_screen_hash,
        "step_idx": edge.step_idx,
    }
