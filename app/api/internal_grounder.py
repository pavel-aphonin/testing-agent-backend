"""/api/internal/grounder — host-services + worker grounder endpoints.

PER-164: the worker needs to know **where** to send grounding
requests (port + path) and **how** to parse the response (regex +
coord space). The host-services bash launcher needs to know
**which** grounder GGUF to start on which port. Both consume the
same row from ``grounder_models`` table — one source of truth, no
duplication in script/env.

Two GET endpoints:

* ``/api/internal/grounder/config`` — launcher-style payload with
  gguf_path / mmproj_path / endpoint_port / image_min_tokens
  (analogous to ``/api/internal/chat-model/config``).
* ``/api/internal/grounder/dispatch`` — worker-style payload with
  endpoint_url (resolved from port if not overridden) / prompt_template
  / response_regex / tap_at_coord_space — everything the worker needs
  to invoke the grounder and parse the answer.

Both pick the single ``is_active=true`` row. If none — 404, and the
caller decides whether to fall back to the chat-LLM's own
coordinate output (today's path) or to fail explicitly.

Auth: ``WORKER_TOKEN`` bearer (same as chat-model/config — both
endpoints are infra-trusted callers, not user-facing).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_runs import require_worker_token
from app.db import get_async_session
from app.models.grounder_model import GrounderModel


router = APIRouter(prefix="/api/internal/grounder", tags=["internal"])


class GrounderLauncherConfig(BaseModel):
    """Minimal contract for ``start-host-services.sh`` — what to start, where, with what flags."""

    name: str
    gguf_path: str
    mmproj_path: str | None = None
    endpoint_port: int
    max_context_tokens: int = 16384
    image_min_tokens: int | None = None


class GrounderDispatchConfig(BaseModel):
    """Worker-side contract — what the grounder expects + how to parse what it returns.

    ``endpoint_url`` is the resolved HTTP base for the grounder
    (chat-completions API). If the DB row sets ``endpoint_url``
    explicitly we trust it (remote grounders); otherwise we
    construct ``http://localhost:{port}`` because grounders run
    on the same host as the worker by convention.
    """

    name: str
    endpoint_url: str
    tap_at_coord_space: str
    response_format: str
    response_regex: str
    prompt_template: str
    default_temperature: float
    default_top_p: float
    screenshot_max_dim: int | None = None
    image_min_tokens: int | None = None


async def _active_grounder(session: AsyncSession) -> GrounderModel:
    """Pick the single active grounder, 404 if none.

    If admin left several rows active (misconfiguration), we take
    the most recently uploaded — same tiebreak as chat-model/config.
    """
    q = (
        select(GrounderModel)
        .where(GrounderModel.is_active.is_(True))
        .order_by(GrounderModel.uploaded_at.desc())
        .limit(1)
    )
    row = (await session.execute(q)).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no active grounder found in grounder_models",
        )
    return row


@router.get(
    "/config",
    response_model=GrounderLauncherConfig,
    dependencies=[Depends(require_worker_token)],
)
async def active_grounder_launcher_config(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> GrounderLauncherConfig:
    """Return launcher config (paths + port + token budget) for the active grounder."""
    row = await _active_grounder(session)
    return GrounderLauncherConfig(
        name=row.name,
        gguf_path=row.gguf_path,
        mmproj_path=row.mmproj_path,
        endpoint_port=int(row.endpoint_port),
        max_context_tokens=int(row.max_context_tokens or 16384),
        image_min_tokens=row.image_min_tokens,
    )


@router.get(
    "/dispatch",
    response_model=GrounderDispatchConfig,
    dependencies=[Depends(require_worker_token)],
)
async def active_grounder_dispatch_config(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> GrounderDispatchConfig:
    """Return dispatch config (URL + parser + prompt template) for the worker."""
    row = await _active_grounder(session)
    endpoint = row.endpoint_url or f"http://localhost:{int(row.endpoint_port)}"
    return GrounderDispatchConfig(
        name=row.name,
        endpoint_url=endpoint,
        tap_at_coord_space=row.tap_at_coord_space,
        response_format=row.response_format,
        response_regex=row.response_regex,
        prompt_template=row.prompt_template,
        default_temperature=float(row.default_temperature),
        default_top_p=float(row.default_top_p),
        screenshot_max_dim=row.screenshot_max_dim,
        image_min_tokens=row.image_min_tokens,
    )
