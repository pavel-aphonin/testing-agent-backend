"""/api/runs/{run_id}/defects and /api/internal/defects.

Two endpoints:

- ``GET /api/runs/{run_id}/defects`` — list defects for a run, filterable
  by priority/severity *code* and kind. Used by the Defects tab on the
  run results page.
- ``POST /api/internal/defects`` — worker posts a new defect it detected.
  Protected by WORKER_TOKEN, same as other ``/internal`` endpoints.

PER-120: priority and severity are now reference rows. Filters accept
their codes (``urgent``, ``blocker``, …) so the URL stays human-readable;
sort order comes from the referenced row's ``sort_order`` column.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_runs import require_worker_token
from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.defect import (
    DefectKind,
    DefectModel,
    DefectPriorityRef,
    DefectSeverityRef,
)
from app.models.run import Run
from app.models.user import User
from app.schemas.defect import DefectCreate, DefectRead

logger = logging.getLogger(__name__)

# Public router — mounted under /api (defects are listed per run)
public_router = APIRouter(prefix="/api/runs", tags=["defects"])
# Worker-token router — mounted under /api/internal
internal_router = APIRouter(
    prefix="/api/internal/defects", tags=["defects", "internal"]
)


# Codes used when the worker posts a defect with an unknown priority /
# severity code. Match the migration seed (``medium`` / ``major``).
_DEFAULT_PRIORITY_CODE = "medium"
_DEFAULT_SEVERITY_CODE = "major"


async def _resolve_ref(
    session: AsyncSession,
    model: type[DefectPriorityRef] | type[DefectSeverityRef],
    code: str,
    fallback_code: str,
) -> DefectPriorityRef | DefectSeverityRef:
    """Look the reference row up by ``code``; fall back to the default
    if unknown. Logs the miss so the operator can fix the agent prompt
    instead of silently demoting every defect."""
    row = (
        await session.execute(select(model).where(model.code == code))
    ).scalar_one_or_none()
    if row is not None:
        return row
    logger.warning(
        "Unknown %s code %r posted by worker; falling back to %r",
        model.__tablename__, code, fallback_code,
    )
    fallback = (
        await session.execute(select(model).where(model.code == fallback_code))
    ).scalar_one_or_none()
    if fallback is None:
        # Both the requested code AND the default are missing — the
        # reference table is broken. Surface it instead of crashing on
        # the FK insert with a less-helpful error.
        raise HTTPException(
            status_code=500,
            detail=(
                f"{model.__tablename__} reference is empty or missing "
                f"the {fallback_code!r} default; reseed the migration."
            ),
        )
    return fallback


@public_router.get("/{run_id}/defects", response_model=list[DefectRead])
async def list_run_defects(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    priority: Annotated[
        str | None,
        Query(
            description=(
                "Filter by priority code (e.g. `urgent`, `high`); "
                "omit for all."
            ),
        ),
    ] = None,
    severity: Annotated[
        str | None,
        Query(
            description=(
                "Filter by severity code (e.g. `blocker`, `critical`); "
                "omit for all."
            ),
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Query(description="Filter by defect kind (functional, ui, ...)"),
    ] = None,
) -> list[DefectModel]:
    """List defects for a run, optionally filtered by priority code,
    severity code, and kind.

    Results are sorted by ``priority.sort_order`` ascending (most urgent
    first), then by step order — matches how a QA triages.
    """
    run = (
        await session.execute(select(Run).where(Run.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    from app.api.runs import _can_access_run
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")

    stmt = select(DefectModel).where(DefectModel.run_id == run_id)
    if priority:
        stmt = stmt.join(DefectPriorityRef, DefectModel.priority_id == DefectPriorityRef.id).where(
            DefectPriorityRef.code == priority
        )
    if severity:
        stmt = stmt.join(DefectSeverityRef, DefectModel.severity_id == DefectSeverityRef.id).where(
            DefectSeverityRef.code == severity
        )
    if kind:
        stmt = stmt.where(DefectModel.kind == kind)

    # Highest urgency first, then chronological. Sort comes from the
    # referenced priority row so admins can re-order the scale without
    # touching defect rows.
    stmt = stmt.join(
        DefectPriorityRef, DefectModel.priority_id == DefectPriorityRef.id
    ).order_by(
        DefectPriorityRef.sort_order.asc(),
        DefectModel.step_idx.asc(),
    )

    return list((await session.execute(stmt)).scalars().unique().all())


@internal_router.post(
    "",
    response_model=DefectRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_worker_token)],
)
async def create_defect(
    payload: DefectCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DefectModel:
    """Worker posts a defect it detected during exploration.

    The worker's defect detector (LLM analysis of an observed failure)
    fills in title/description/priority_code/severity_code/kind. Infra
    noise (network drops, unloaded screens) is filtered out on the
    worker side — it should never reach here with kind=infra_noise
    unless we explicitly want to keep it for debugging.
    """
    # Verify run exists (FK would catch it anyway, but this gives a
    # nicer error than a DB constraint violation).
    run_exists = (
        await session.execute(select(Run.id).where(Run.id == payload.run_id))
    ).scalar_one_or_none()
    if run_exists is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Clamp `kind` to known values — same defensive treatment we used
    # for the old DefectPriority enum. Unknown kinds become
    # `functional` so the defect still lands and gets reviewed.
    kind = payload.kind
    try:
        DefectKind(kind)
    except ValueError:
        kind = DefectKind.FUNCTIONAL.value

    priority = await _resolve_ref(
        session, DefectPriorityRef,
        payload.priority_code, _DEFAULT_PRIORITY_CODE,
    )
    severity = await _resolve_ref(
        session, DefectSeverityRef,
        payload.severity_code, _DEFAULT_SEVERITY_CODE,
    )

    defect = DefectModel(
        run_id=payload.run_id,
        step_idx=payload.step_idx,
        screen_id_hash=payload.screen_id_hash,
        screen_name=payload.screen_name,
        priority_id=priority.id,
        severity_id=severity.id,
        kind=kind,
        title=payload.title,
        description=payload.description,
        screenshot_path=payload.screenshot_path,
        llm_analysis_json=payload.llm_analysis_json,
    )
    session.add(defect)
    await session.commit()
    await session.refresh(defect)

    # Fire the "defect.created" event for any app installed in the
    # owning workspace that subscribes to it via manifest.hooks.
    try:
        from app.models.run import Run as _Run
        run = await session.get(_Run, defect.run_id)
        if run is not None:
            from app.services.app_events import emit_event
            await emit_event(
                "defect.created",
                {
                    "defect_id": str(defect.id),
                    "run_id": str(defect.run_id),
                    "priority": priority.code,
                    "severity": severity.code,
                    "kind": defect.kind,
                    "title": defect.title,
                    "description": defect.description,
                    "screen_name": defect.screen_name,
                },
                workspace_id=run.workspace_id,
            )
    except Exception:
        # Never fail the defect write because a webhook misbehaves.
        logger.exception("emit_event defect.created failed")

    return defect
