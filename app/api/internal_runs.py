"""/api/internal/runs/* — endpoints for the explorer worker daemon.

The worker is a separate Python process running on the host (not in the
backend container, because it needs to spawn iOS simulator tools). It
authenticates with a shared Bearer token (WORKER_TOKEN env var), polls
for pending runs, and posts events as it discovers screens and edges.

These endpoints are NOT exposed to end users — they sit under
/api/internal so it's obvious from the URL alone. The user-facing
JWT auth is intentionally ignored here.
"""

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models.run import Edge, Run, RunStatus, Screen
from app.redis_bus import get_redis, publish_run_event
from app.schemas.run import SimulatorConfigReport
from app.schemas.run_event import RunClaimResponse, RunEventIn

router = APIRouter(prefix="/api/internal/runs", tags=["internal"])


def require_worker_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer token check for all worker endpoints. No JWT, no fastapi-users."""
    expected = f"Bearer {settings.worker_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Worker token required",
        )


@router.post(
    "/claim",
    response_model=RunClaimResponse | None,
    dependencies=[Depends(require_worker_token)],
)
async def claim_next_pending_run(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> RunClaimResponse | None:
    """Atomically claim the oldest pending run and transition it to running.

    Returns 204 No Content if there are no pending runs (worker should
    sleep and retry). Returns the run config if a claim was successful.
    """
    # SELECT ... FOR UPDATE SKIP LOCKED would be ideal but pgvector image
    # supports it. For now we keep it simple — single worker assumption.
    result = await session.execute(
        select(Run)
        .where(Run.status == RunStatus.PENDING.value)
        .order_by(Run.created_at.asc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None

    run.status = RunStatus.RUNNING.value
    run.started_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)

    # Tell anyone watching that the run started.
    await publish_run_event(
        str(run.id),
        {
            "type": "status_change",
            "new_status": RunStatus.RUNNING.value,
            "timestamp": run.started_at.isoformat(),
        },
    )

    # Load test data entries — the agent uses these to fill form fields.
    from app.models.test_data import TestData
    td_rows = (
        await session.execute(select(TestData.key, TestData.value))
    ).all()
    test_data = {row.key: row.value for row in td_rows}

    # Expand scenario IDs into full step payloads. The worker walks these
    # before falling back to free exploration.
    scenarios: list[dict] = []
    if run.scenario_ids:
        from app.models.scenario import Scenario
        from uuid import UUID as _UUID
        sc_rows = (
            await session.execute(
                select(Scenario)
                .where(Scenario.id.in_([_UUID(sid) for sid in run.scenario_ids]))
                .where(Scenario.is_active.is_(True))
            )
        ).scalars().all()
        # Preserve the order the user specified, not DB order.
        sc_by_id = {str(s.id): s for s in sc_rows}
        for sid in run.scenario_ids:
            s = sc_by_id.get(sid)
            if s is None:
                continue
            scenarios.append({
                "id": str(s.id),
                "title": s.title,
                "steps": (s.steps_json or {}).get("steps", []),
            })

    return RunClaimResponse(
        run_id=run.id,
        bundle_id=run.bundle_id,
        device_id=run.device_id,
        platform=run.platform,
        mode=run.mode,
        max_steps=run.max_steps,
        c_puct=run.c_puct,
        rollout_depth=run.rollout_depth,
        # V2 simulator lifecycle fields
        device_type=run.device_type,
        os_version=run.os_version,
        app_file_path=run.app_file_path,
        # Test data for the agent to use when filling forms
        test_data=test_data,
        # Pre-scripted scenarios to execute before free exploration
        scenarios=scenarios,
    )


@router.post(
    "/{run_id}/event",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_worker_token)],
)
async def post_run_event(
    run_id: UUID,
    event: RunEventIn,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Worker posts one event from the live exploration.

    The event is persisted to Postgres (mutating Run, Screen, or Edge as
    appropriate) and then re-broadcast to Redis so the WebSocket
    subscribers (browsers watching this run) receive it in real time.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # Don't accept events for finished runs — that would be a worker bug.
    terminal = {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    if run.status in terminal and event.type != "log":
        raise HTTPException(
            status_code=409,
            detail=f"Run is already in terminal state: {run.status}",
        )

    timestamp = event.timestamp or datetime.now(timezone.utc)

    if event.type == "status_change" and event.new_status:
        run.status = event.new_status
        if event.new_status == RunStatus.RUNNING.value and run.started_at is None:
            run.started_at = timestamp
        if event.new_status in terminal:
            run.finished_at = timestamp
            if event.message:
                run.error_message = event.message

    elif event.type == "screen_discovered":
        if not event.screen_id_hash:
            raise HTTPException(
                status_code=422, detail="screen_id_hash is required"
            )
        # Upsert: if a screen with this hash already exists for this run,
        # bump its visit_count instead of inserting a duplicate.
        existing = await session.execute(
            select(Screen).where(
                Screen.run_id == run.id,
                Screen.screen_id_hash == event.screen_id_hash,
            )
        )
        screen = existing.scalar_one_or_none()
        # Save screenshot base64 to disk if provided
        saved_screenshot_path = event.screenshot_path
        if event.screenshot_b64 and not saved_screenshot_path:
            try:
                screenshots_dir = Path(settings.app_uploads_dir) / "screenshots" / str(run.id)
                screenshots_dir.mkdir(parents=True, exist_ok=True)
                img_path = screenshots_dir / f"{event.screen_id_hash}.png"
                img_path.write_bytes(base64.b64decode(event.screenshot_b64))
                saved_screenshot_path = f"screenshots/{run.id}/{event.screen_id_hash}.png"
            except Exception:
                pass

        if screen is None:
            screen = Screen(
                run_id=run.id,
                screen_id_hash=event.screen_id_hash,
                name=event.screen_name or "(unnamed)",
                visit_count=1,
                screenshot_path=saved_screenshot_path,
            )
            session.add(screen)
        else:
            screen.visit_count += 1
            if saved_screenshot_path:
                screen.screenshot_path = saved_screenshot_path

    elif event.type == "edge_discovered":
        if not event.source_screen_hash or not event.target_screen_hash or not event.action_type:
            raise HTTPException(
                status_code=422,
                detail="source_screen_hash, target_screen_hash, and action_type are required",
            )
        edge = Edge(
            run_id=run.id,
            source_screen_hash=event.source_screen_hash,
            target_screen_hash=event.target_screen_hash,
            action_type=event.action_type,
            action_details_json=event.action_details,
            success=event.success if event.success is not None else True,
            step_idx=event.step_idx,
        )
        session.add(edge)

    elif event.type == "stats_update" and event.stats is not None:
        run.stats_json = event.stats

    elif event.type == "error":
        run.error_message = event.message
        run.status = RunStatus.FAILED.value
        run.finished_at = timestamp

    await session.commit()

    # Broadcast the event to anyone watching this run.
    await publish_run_event(
        str(run.id),
        {
            "type": event.type,
            "step_idx": event.step_idx,
            "timestamp": timestamp.isoformat(),
            "new_status": event.new_status,
            "screen_id_hash": event.screen_id_hash,
            "screen_name": event.screen_name,
            "source_screen_hash": event.source_screen_hash,
            "target_screen_hash": event.target_screen_hash,
            "action_type": event.action_type,
            "action_details": event.action_details,
            "success": event.success,
            "message": event.message,
            "stats": event.stats,
        },
    )

    return {"accepted": True}


@router.post(
    "/config",
    dependencies=[Depends(require_worker_token)],
)
async def report_simulator_config(
    payload: SimulatorConfigReport,
) -> dict:
    """Worker reports available runtimes + device types on startup.

    Cached in Redis (5-minute TTL) and served to the admin UI via
    ``GET /api/admin/devices/available``. This lets the admin see what
    the worker host has installed and pick configs to expose.
    """
    redis = get_redis()
    await redis.setex(
        "simulator:config",
        300,
        payload.model_dump_json(),
    )
    return {"accepted": True}
