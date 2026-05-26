"""PER-184: shared envelope + helper for server-side paginated list
endpoints.

Why a dedicated module instead of inlining a Pydantic generic in each
endpoint:
    * The envelope shape (items / total / page / per_page / has_more)
      is what the frontend table component will key off — having one
      type guarantees every paginated list looks identical on the wire.
    * ``paginate_query`` co-locates the COUNT(*) and the OFFSET/LIMIT
      slice so we don't drift between endpoints (some forgetting the
      count, some clamping per_page differently).
    * Importing ``Paginated[T]`` keeps each endpoint's signature short
      — ``response_model=Paginated[RunRead]`` reads like the legacy
      ``response_model=list[RunRead]``.

This is the **opt-in** pilot per the ticket. Existing endpoints keep
their bare ``list[T]`` shape until they're individually migrated; we
do not attempt a sweeping rewrite here.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

T = TypeVar("T")

# Defaults picked so the legacy "no ?page= supplied" caller still gets
# a useful first page without wedging on huge workspaces. 50 mirrors
# what the Runs/Scenarios tables render comfortably; 200 caps API
# abuse without making genuine "give me the whole workspace" jobs
# impossible (those will move to cursor pagination later).
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200


class Paginated(BaseModel, Generic[T]):
    """Standard envelope for paginated list endpoints.

    ``has_more`` is derived (``page * per_page < total``) and shipped so
    clients can render «Load more» / «Next page» without re-doing the
    math. Keeping it in the response also means a client that only
    needs the next-page hint doesn't have to look at ``total`` at all
    — which matters when we eventually offer cursor pagination where
    ``total`` may be unset.
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    per_page: int = Field(ge=1)
    has_more: bool


def _clamp(page: int, per_page: int) -> tuple[int, int]:
    """Defensive bounds so callers can't accidentally request page=0
    (Postgres OFFSET of -50 errors at runtime) or per_page=100000
    (defeats the point of paginating)."""
    page = max(1, page)
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    return page, per_page


async def paginate_query(
    session: AsyncSession,
    query: Select,
    page: int,
    per_page: int,
) -> tuple[list, int]:
    """Run ``query`` as a paginated SELECT and return ``(rows, total)``.

    Two round-trips instead of one:
        1. ``SELECT COUNT(*) FROM (<original-without-order>) sub`` —
           strips ORDER BY because it doesn't affect cardinality and
           keeps EXPLAIN output readable.
        2. ``SELECT ... ORDER BY ... OFFSET (page-1)*per_page LIMIT
           per_page`` — the actual page slice.

    We don't try to be clever with window-functions (``COUNT(*) OVER ()``)
    because pgsql plans the page-slice and the count-over-window as a
    single sort over the full result set, which is *worse* than the
    two-trip plan on workspaces large enough to need pagination in
    the first place.
    """
    page, per_page = _clamp(page, per_page)

    count_q = select(func.count()).select_from(query.order_by(None).subquery())
    total = int((await session.execute(count_q)).scalar_one())

    page_q = query.offset((page - 1) * per_page).limit(per_page)
    rows = list((await session.execute(page_q)).scalars().all())
    return rows, total


def make_envelope(items: list, total: int, page: int, per_page: int) -> dict:
    """Bundle a paginated result into the dict shape ``Paginated[T]``
    validates against.

    Items stay as plain ORM objects — Pydantic's ``from_attributes=True``
    on ``Paginated[T]`` handles ORM → response_model conversion the
    same way it does for ``response_model=list[RunRead]``. Doing the
    serialisation here would force every endpoint to re-import its own
    Read schema just to build the envelope.
    """
    page, per_page = _clamp(page, per_page)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_more": (page * per_page) < total,
    }
