"""/api/runs/{run_id}/defects and /api/internal/defects.

Two endpoints:
- GET /api/runs/{run_id}/defects — list defects for a run, filterable by priority/kind.
  Used by the Defects tab on the run results page.
- POST /api/internal/defects — worker posts a new defect it detected. Protected
  by WORKER_TOKEN, same as other /internal endpoints.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.api.internal_runs import require_worker_token
from app.db import get_async_session
from app.models.defect import DefectModel, DefectPriority
from app.models.run import Run
from app.models.user import User
from app.schemas.defect import DefectCreate, DefectRead

# Public router — mounted under /api (defects are listed per run)
public_router = APIRouter(prefix="/api/runs", tags=["defects"])
# Worker-token router — mounted under /api/internal
internal_router = APIRouter(
    prefix="/api/internal/defects", tags=["defects", "internal"]
)


def _is_admin(user: User) -> bool:
    return user.role == "admin"


@public_router.get("/{run_id}/defects", response_model=list[DefectRead])
async def list_run_defects(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    priority: Annotated[
        str | None,
        Query(description="Filter: P0, P1, P2, P3, or omit for all"),
    ] = None,
    kind: Annotated[
        str | None,
        Query(description="Filter by defect kind (functional, ui, ...)"),
    ] = None,
) -> list[DefectModel]:
    """List defects for a run, optionally filtered by priority and kind.

    Results are sorted by priority (P0 first) then by step order. The UI's
    Defects tab uses this for the triage workflow.
    """
    # Authorization: admins see all, owners see their own.
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _is_admin(user) and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")

    stmt = select(DefectModel).where(DefectModel.run_id == run_id)
    if priority:
        stmt = stmt.where(DefectModel.priority == priority)
    if kind:
        stmt = stmt.where(DefectModel.kind == kind)
    # P0 first, then chronological — matches how a QA triages.
    stmt = stmt.order_by(DefectModel.priority.asc(), DefectModel.step_idx.asc())

    return list((await session.execute(stmt)).scalars().all())


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

    The worker's defect detector (LLM analysis of an observed failure) fills
    in title/description/priority/kind. Infra noise (network drops, unloaded
    screens) is filtered out on the worker side — it should never reach here
    with kind=infra_noise unless we explicitly want to keep it for debugging.
    """
    # Verify run exists (FK would catch it anyway, but this gives a nicer error).
    run_exists = (
        await session.execute(select(Run.id).where(Run.id == payload.run_id))
    ).scalar_one_or_none()
    if run_exists is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Clamp priority to known values — the LLM sometimes hallucinates "P4".
    priority = payload.priority
    try:
        DefectPriority(priority)
    except ValueError:
        priority = DefectPriority.P2.value

    defect = DefectModel(
        run_id=payload.run_id,
        step_idx=payload.step_idx,
        screen_id_hash=payload.screen_id_hash,
        screen_name=payload.screen_name,
        priority=priority,
        kind=payload.kind,
        title=payload.title,
        description=payload.description,
        screenshot_path=payload.screenshot_path,
        llm_analysis_json=payload.llm_analysis_json,
    )
    session.add(defect)
    await session.commit()
    await session.refresh(defect)

    # Fire the "defect.created" event for any app installed in the owning
    # workspace that subscribes to it via manifest.hooks.
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
                    "priority": defect.priority,
                    "kind": defect.kind,
                    "title": defect.title,
                    "description": defect.description,
                    "screen_name": defect.screen_name,
                },
                workspace_id=run.workspace_id,
            )
    except Exception:
        # Never fail the defect write because a webhook misbehaves.
        import logging
        logging.getLogger(__name__).exception("emit_event defect.created failed")

    return defect
