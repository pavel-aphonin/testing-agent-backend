"""/api/admin/system-prompts — admin CRUD for editable LLM prompts.

Two routers:
    * ``admin_router`` (``/api/admin/system-prompts``) — listing + read
      + edit + reset, gated by ``users.view`` (admin).
    * ``internal_router`` (``/api/internal/system-prompts``) — read-only
      lookup by code for the worker, gated by the same WORKER_TOKEN as
      the rest of ``/api/internal/*``.

Both surfaces return ``SystemPromptRead`` so the admin UI and the worker
share one shape — keeps the contract honest.

PER-111: editing rules
    * ``content`` must contain every placeholder declared in
      ``placeholders``. Otherwise the worker would substitute against
      a hole and end up sending a malformed prompt. Reject 422.
    * ``code`` / ``default_content`` / ``placeholders`` / ``is_builtin``
      are immutable at runtime — the seed migration owns them.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_runs import require_worker_token
from app.auth.users import require_permission
from app.db import get_async_session
from app.models.system_prompt import SystemPrompt
from app.models.user import User
from app.schemas.system_prompt import SystemPromptRead, SystemPromptUpdate


admin_router = APIRouter(
    prefix="/api/admin/system-prompts", tags=["system-prompts"]
)
internal_router = APIRouter(
    prefix="/api/internal/system-prompts", tags=["system-prompts", "internal"]
)


def _validate_placeholders(content: str, required: list[str]) -> list[str]:
    """Return the list of placeholders declared as required but missing
    from ``content``. An empty result means content is valid."""
    missing: list[str] = []
    for name in required:
        # Match both ``{{name}}`` and ``{{ name }}`` (loose whitespace).
        pattern = r"\{\{\s*" + re.escape(name) + r"\s*\}\}"
        if not re.search(pattern, content):
            missing.append(name)
    return missing


# ── Admin endpoints ─────────────────────────────────────────────────────────


@admin_router.get("", response_model=list[SystemPromptRead])
async def list_prompts(
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[SystemPrompt]:
    """List every seeded + custom prompt slot. Admin only."""
    result = await session.execute(
        select(SystemPrompt).order_by(SystemPrompt.code.asc())
    )
    return list(result.scalars().all())


@admin_router.get("/{code}", response_model=SystemPromptRead)
async def get_prompt_admin(
    code: str,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SystemPrompt:
    row = (
        await session.execute(
            select(SystemPrompt).where(SystemPrompt.code == code)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    return row


@admin_router.patch("/{code}", response_model=SystemPromptRead)
async def update_prompt(
    code: str,
    payload: SystemPromptUpdate,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SystemPrompt:
    row = (
        await session.execute(
            select(SystemPrompt).where(SystemPrompt.code == code)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    patch = payload.model_dump(exclude_unset=True)
    if "content" in patch and patch["content"] is not None:
        missing = _validate_placeholders(patch["content"], row.placeholders)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "missing_placeholders",
                    "message": (
                        "В тексте должны присутствовать все обязательные "
                        "плейсхолдеры — без них воркер не сможет подставить "
                        "значения на исполнении."
                    ),
                    "missing": missing,
                },
            )
    for field, value in patch.items():
        if value is not None:
            setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return row


@admin_router.post("/{code}/reset", response_model=SystemPromptRead)
async def reset_prompt(
    code: str,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SystemPrompt:
    """Revert ``content`` to the migration's ``default_content``.

    Safety net for the operator: if an edit broke the agent, they can
    always go back to a known-good text without rolling back a migration.
    """
    row = (
        await session.execute(
            select(SystemPrompt).where(SystemPrompt.code == code)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    row.content = row.default_content
    await session.commit()
    await session.refresh(row)
    return row


# ── Worker endpoint ─────────────────────────────────────────────────────────


@internal_router.get(
    "/{code}",
    response_model=SystemPromptRead,
    dependencies=[Depends(require_worker_token)],
)
async def get_prompt_internal(
    code: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SystemPrompt:
    """Worker-side lookup. Returns 404 if the requested slot was never
    seeded — worker falls back to its own default in that case."""
    row = (
        await session.execute(
            select(SystemPrompt).where(SystemPrompt.code == code)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    return row
