"""/api/runs — list, create, fetch, delete exploration runs.

Permission rules:
    - viewer/tester/admin can list and get their own runs
    - admin can list and get any run
    - tester/admin can create runs (viewer cannot)
    - admin can delete any run; tester only their own; viewer cannot delete
"""

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.config import settings
from app.db import get_async_session
from app.models.device_config import DeviceConfig
from app.models.run import Edge, Run, RunStatus, Screen
from app.models.user import User
from app.schemas.run import RunCreate, RunCreateV2, RunRead, RunResultRead

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _has_perm(user: User, perm: str) -> bool:
    return perm in user.permissions


@router.get("", response_model=list[RunRead])
async def list_runs(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
) -> list[Run]:
    """List runs.

    If `workspace_id` query param is set, returns only runs in that
    workspace (and the caller must be a member). Otherwise returns
    runs the caller created (admins see all).
    """
    q = select(Run).order_by(Run.created_at.desc())

    if workspace_id is not None:
        # Verify membership unless admin
        if not _has_perm(user, "users.view"):
            from app.models.workspace import WorkspaceMember
            mem_result = await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
            if mem_result.scalar_one_or_none() is None:
                raise HTTPException(403, "Not a member of this workspace")
        q = q.where(Run.workspace_id == workspace_id)
    elif not _has_perm(user, "users.view"):
        q = q.where(Run.user_id == user.id)

    result = await session.execute(q)
    return list(result.scalars().all())


@router.post(
    "",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runs.create"))],
)
async def create_run(
    payload: RunCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Create a new exploration run in `pending` status.

    The actual exploration is started by Block 4 (subprocess runner).
    For now this endpoint just records the request so the UI can list it.
    """
    run = Run(
        user_id=user.id,
        bundle_id=payload.bundle_id,
        device_id=payload.device_id,
        platform=payload.platform,
        mode=payload.mode,
        max_steps=payload.max_steps,
        c_puct=payload.c_puct,
        rollout_depth=payload.rollout_depth,
        status=RunStatus.PENDING.value,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.post(
    "/v2",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runs.create"))],
)
async def create_run_v2(
    payload: RunCreateV2,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Create a run with automatic simulator provisioning (V2 flow).

    The worker will create a fresh simulator/AVD, install the uploaded
    app, launch it, and tear it down after the run completes.
    """
    # Verify the app upload exists
    meta_path = Path(settings.app_uploads_dir) / payload.app_file_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(400, "App upload not found. Upload the app first.")
    meta = json.loads(meta_path.read_text())

    # Verify the device config exists and is active
    result = await session.execute(
        select(DeviceConfig).where(DeviceConfig.id == payload.device_config_id)
    )
    device_config = result.scalar_one_or_none()
    if device_config is None:
        raise HTTPException(400, "Device configuration not found")
    if not device_config.is_active:
        raise HTTPException(400, "This device configuration is disabled")

    run = Run(
        user_id=user.id,
        title=(payload.title or "").strip() or None,
        bundle_id=meta["bundle_id"],
        device_id="__PENDING__",  # populated by worker after sim creation
        platform=meta["platform"],
        mode=payload.mode,
        max_steps=payload.max_steps,
        c_puct=payload.c_puct,
        rollout_depth=payload.rollout_depth,
        status=RunStatus.PENDING.value,
        device_type=device_config.device_identifier,
        os_version=device_config.os_identifier,
        app_file_path=meta["app_relative_path"],
        # Empty list = free exploration only. Non-empty = run scenarios first.
        scenario_ids=[str(sid) for sid in payload.scenario_ids] or None,
        pbt_enabled=payload.pbt_enabled,
        workspace_id=payload.workspace_id,
    )
    session.add(run)
    await session.flush()

    # Persist any run-scoped attribute values shipped with the request.
    if payload.attribute_values:
        from app.models.attribute import Attribute, AttributeValue
        from uuid import UUID as _UUID
        for attr_id_str, val in payload.attribute_values.items():
            try:
                attr_id = _UUID(attr_id_str)
            except (ValueError, TypeError):
                continue
            attr = await session.get(Attribute, attr_id)
            if attr is None or attr.applies_to != "run":
                continue
            if attr.is_required and (val is None or val == "" or val == []):
                raise HTTPException(
                    422,
                    f"Атрибут «{attr.name}» обязателен для заполнения",
                )
            session.add(AttributeValue(
                attribute_id=attr_id,
                entity_type="run",
                entity_id=run.id,
                value=val,
            ))

    await session.commit()
    await session.refresh(run)
    return run


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _has_perm(user, "users.view") and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    return run


@router.get("/{run_id}/results", response_model=RunResultRead)
async def get_run_results(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> RunResultRead:
    """Return the run together with all discovered screens and edges.

    Used by the Results page after exploration finishes. The same row-level
    permission rules as GET /api/runs/{id} apply.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _has_perm(user, "users.view") and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")

    screens_q = await session.execute(
        select(Screen)
        .where(Screen.run_id == run_id)
        .order_by(Screen.first_seen_at.asc())
    )
    edges_q = await session.execute(
        select(Edge).where(Edge.run_id == run_id).order_by(Edge.step_idx.asc())
    )

    return RunResultRead(
        run=RunRead.model_validate(run),
        screens=[s for s in screens_q.scalars().all()],
        edges=[e for e in edges_q.scalars().all()],
    )


@router.get("/{run_id}/screens/{screen_hash}/screenshot")
async def get_screen_screenshot(
    run_id: UUID,
    screen_hash: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Serve a screenshot PNG for a specific screen."""
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _has_perm(user, "users.view") and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")

    screen_result = await session.execute(
        select(Screen).where(Screen.run_id == run_id, Screen.screen_id_hash == screen_hash)
    )
    screen = screen_result.scalar_one_or_none()
    if screen is None or not screen.screenshot_path:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    file_path = Path(settings.app_uploads_dir) / screen.screenshot_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing")

    return FileResponse(file_path, media_type="image/png")


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Mark a running run as CANCELLED.

    The worker sees the terminal status on its next heartbeat / event post
    (via the 409 response from /internal/runs/{id}/event) and stops its loop.
    Idempotent — calling on an already-terminal run is a no-op.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _has_perm(user, "users.view") and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    if not _has_perm(user, "runs.cancel"):
        raise HTTPException(status_code=403, detail="Missing runs.cancel permission")

    terminal = {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    if run.status in terminal:
        return {"status": run.status, "message": "already terminal"}

    from datetime import datetime, timezone
    run.status = RunStatus.CANCELLED.value
    run.finished_at = datetime.now(timezone.utc)
    await session.commit()

    # Broadcast so subscribed UIs see the status flip immediately.
    from app.redis_bus import publish_run_event
    await publish_run_event(
        str(run.id),
        {
            "type": "status_change",
            "new_status": RunStatus.CANCELLED.value,
            "timestamp": run.finished_at.isoformat(),
        },
    )
    return {"status": run.status}


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    """Delete a run. If it's still running, cancel it first so the worker stops."""
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _has_perm(user, "users.view") and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    if not _has_perm(user, "runs.delete"):
        raise HTTPException(status_code=403, detail="Missing runs.delete permission")

    # If still active, flip to CANCELLED so the worker stops on its next event.
    # We don't wait for the worker to acknowledge — the DELETE still proceeds.
    active = {RunStatus.PENDING.value, RunStatus.RUNNING.value}
    if run.status in active:
        from datetime import datetime, timezone
        from app.redis_bus import publish_run_event
        run.status = RunStatus.CANCELLED.value
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        await publish_run_event(
            str(run.id),
            {
                "type": "status_change",
                "new_status": RunStatus.CANCELLED.value,
                "timestamp": run.finished_at.isoformat(),
            },
        )
        # Refetch to continue deletion
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one()

    await session.delete(run)
    await session.commit()
