"""Validation helpers for scenario step payloads.

Owns the regex that recognises ``{{test_data.KEY}}`` placeholders
inside scenario step values, plus the lookup helper that resolves
those keys against a workspace's TestData rows. Used both by the
scenarios POST/PATCH endpoint (to refuse unresolved keys at save
time) and by the workers as a future improvement to give a clear
``unresolved placeholder`` warning at run time.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Captures the KEY in ``{{test_data.KEY}}`` (or ``{{ test_data.KEY }}``
# with whitespace). Identical to what the worker uses in
# explorer/llm_loop.py::_substitute_test_data — keep them in sync if
# the format ever changes.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*test_data\.(\w+)\s*\}\}")


def extract_placeholders(steps: list[dict[str, Any]] | None) -> set[str]:
    """Return the set of ``test_data`` keys referenced anywhere in the
    given step list. Looks at every string-valued field of every step
    so callers don't have to know which fields can contain templating
    (right now: ``value`` and ``expected_result``, but the worker also
    expands titles/descriptions in the LLM prompt)."""
    if not steps:
        return set()
    keys: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        for v in step.values():
            if isinstance(v, str):
                keys.update(_PLACEHOLDER_RE.findall(v))
    return keys


async def find_unresolved_placeholders(
    session: AsyncSession,
    workspace_id: UUID | None,
    steps: list[dict[str, Any]] | None,
) -> list[str]:
    """Return placeholder keys that aren't present in the workspace's
    TestData. ``workspace_id=None`` short-circuits to "no workspace
    scope" — used by legacy scenarios that haven't been ported yet;
    they get a free pass instead of a noisy error."""
    referenced = extract_placeholders(steps)
    if not referenced:
        return []
    if workspace_id is None:
        return []  # legacy scope: don't break old scenarios on save
    from app.models.test_data import TestData
    rows = (
        await session.execute(
            select(TestData.key).where(TestData.workspace_id == workspace_id)
        )
    ).all()
    available = {r[0] for r in rows}
    return sorted(referenced - available)
