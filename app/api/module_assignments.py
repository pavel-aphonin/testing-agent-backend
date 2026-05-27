"""PER-193: /api/admin/module-assignments — per-role LLM picker.

Two endpoints:

* ``GET /api/admin/module-assignments`` — list all 11 rows in a
  deterministic order (matches ``ALL_MODULE_ROLES``). Always
  returns 11 even on a fresh DB — the migration seeds them with
  ``llm_model_id=NULL``.
* ``PUT /api/admin/module-assignments/{role}`` — upsert (only the
  ``llm_model_id`` is operator-controlled; audit metadata is
  stamped server-side).

Gated by the ``models.edit`` permission — same scope as catalog
CRUD. Read-only ``models.view`` callers get a 403 on PUT but can
GET to inspect current state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.llm_model import LLMModel
from app.models.module_assignment import (
    ALL_MODULE_ROLES,
    ModuleAssignment,
    ModuleRole,
)
from app.models.user import User
from app.schemas.module_assignment import (
    ModuleAssignmentRead,
    ModuleAssignmentUpsert,
)

router = APIRouter(
    prefix="/api/admin/module-assignments",
    tags=["admin-module-assignments"],
)


async def _list_ordered(session: AsyncSession) -> list[ModuleAssignment]:
    """All assignments in ``ALL_MODULE_ROLES`` order.

    Postgres doesn't preserve insert order on its own, so we sort
    in Python by the canonical enum sequence. Costs nothing at
    11 rows and keeps the admin UI stable when new roles are
    added at the end of the enum.
    """
    result = await session.execute(select(ModuleAssignment))
    by_role = {row.role: row for row in result.scalars().all()}
    return [by_role[r.value] for r in ALL_MODULE_ROLES if r.value in by_role]


@router.get("", response_model=list[ModuleAssignmentRead])
async def list_assignments(
    _user: Annotated[User, Depends(require_permission("models.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ModuleAssignment]:
    """List all 11 role assignments. Fresh DBs return rows with
    ``llm_model_id=NULL`` — the migration seeds the table."""
    return await _list_ordered(session)


@router.put("/{role}", response_model=ModuleAssignmentRead)
async def upsert_assignment(
    role: ModuleRole,
    payload: ModuleAssignmentUpsert,
    user: Annotated[User, Depends(require_permission("models.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ModuleAssignment:
    """Assign (or unassign by passing ``llm_model_id=null``) a
    catalog model to a role. Audit columns (``assigned_at``,
    ``assigned_by_user_id``) are stamped server-side.
    """
    # Verify the role row exists (it should — migration seeds 11).
    # If a future role is added in code but the migration that seeds
    # it hasn't run, fail loudly here rather than silently INSERT.
    result = await session.execute(
        select(ModuleAssignment).where(ModuleAssignment.role == role.value)
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise HTTPException(
            500,
            f"Module role {role.value} is declared in code but not seeded "
            f"in module_assignments. Re-run alembic upgrade head.",
        )

    if payload.llm_model_id is not None:
        # Validate the target model exists + is active + (if it
        # declares supported_roles) actually supports this role.
        target = await session.get(LLMModel, payload.llm_model_id)
        if target is None:
            raise HTTPException(404, "LLM model not found")
        if not target.is_active:
            raise HTTPException(
                400, "Cannot assign a disabled model — re-enable it first."
            )
        # Empty supported_roles → legacy/general-purpose, allowed
        # for any role. Non-empty + role not in list → reject.
        if target.supported_roles and role.value not in target.supported_roles:
            raise HTTPException(
                400,
                f"Model '{target.name}' does not declare support for "
                f"role {role.value}. Declared: {sorted(target.supported_roles)}.",
            )

    assignment.llm_model_id = payload.llm_model_id
    assignment.assigned_at = datetime.now(timezone.utc) if payload.llm_model_id else None
    assignment.assigned_by_user_id = user.id if payload.llm_model_id else None
    await session.commit()
    await session.refresh(assignment)
    return assignment
