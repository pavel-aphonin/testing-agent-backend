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
    # SCOPED to the run's workspace: previously this query returned every
    # workspace's keys, which both leaked credentials across tenants and
    # made same-named keys (e.g. "password") collide in unpredictable
    # order. We also ORDER BY created_at DESC + dict-build so within one
    # workspace the freshest value wins on duplicate keys (UI doesn't
    # currently enforce uniqueness; that's a separate cleanup).
    from app.models.test_data import TestData
    td_rows = (
        await session.execute(
            select(TestData.key, TestData.value)
            .where(TestData.workspace_id == run.workspace_id)
            .order_by(TestData.created_at.desc())
        )
    ).all()
    # Dict comprehension iterates in order — earlier (newer) wins, since
    # later entries with the same key would just overwrite. Reverse the
    # iteration to keep the newest first by walking explicitly.
    test_data: dict[str, str] = {}
    for key, value in td_rows:
        test_data.setdefault(key, value)

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
                # PER-35: scope RAG verification to the linked spec
                # documents (empty list = whole workspace corpus).
                "rag_document_ids": [str(d) for d in (s.rag_document_ids or [])],
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
        # Property-based testing of form validation
        pbt_enabled=run.pbt_enabled,
        # PER-40 / PER-41: pre-recorded action chain to play back
        # before exploration. Empty list on regular runs. Worker
        # interprets: replay_actions+max_steps>0 = replay then
        # explore (PER-41); replay_actions+max_steps==0 = replay
        # only (PER-40 with continue_after_replay=False).
        replay_actions=run.replay_actions_json or [],
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
            # If the screen was first seen before its name could be derived
            # (e.g. app still loading) and we now have a better name, upgrade
            # it. We only overwrite known-placeholder names so a real heading
            # is never replaced by something worse.
            placeholders = {"Главный экран", "(unnamed)", "Без имени", ""}
            if (
                event.screen_name
                and screen.name in placeholders
                and event.screen_name not in placeholders
            ):
                screen.name = event.screen_name

    elif event.type == "edge_discovered":
        if not event.source_screen_hash or not event.target_screen_hash or not event.action_type:
            raise HTTPException(
                status_code=422,
                detail="source_screen_hash, target_screen_hash, and action_type are required",
            )
        # Persist optional before/after timeline artefacts (PER-25).
        # Filename uses run_id + step_idx so two edges at the same
        # step (rare but possible after retries) overwrite cleanly
        # rather than piling up. Failures here mustn't break edge
        # insertion — the timeline tab degrades gracefully.
        before_path: str | None = None
        after_path: str | None = None
        try:
            screenshots_dir = Path(settings.app_uploads_dir) / "screenshots" / str(run.id)
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            if event.screenshot_before_b64:
                p = screenshots_dir / f"step{event.step_idx}-before.png"
                p.write_bytes(base64.b64decode(event.screenshot_before_b64))
                before_path = f"screenshots/{run.id}/step{event.step_idx}-before.png"
            if event.screenshot_after_b64:
                p = screenshots_dir / f"step{event.step_idx}-after.png"
                p.write_bytes(base64.b64decode(event.screenshot_after_b64))
                after_path = f"screenshots/{run.id}/step{event.step_idx}-after.png"
        except Exception:
            pass
        edge = Edge(
            run_id=run.id,
            source_screen_hash=event.source_screen_hash,
            target_screen_hash=event.target_screen_hash,
            action_type=event.action_type,
            action_details_json=event.action_details,
            success=event.success if event.success is not None else True,
            step_idx=event.step_idx,
            screenshot_before_path=before_path,
            screenshot_after_path=after_path,
            llm_reasoning=event.llm_reasoning,
            rag_verdict_json=event.rag_verdict_json,  # PER-36
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
            # is_new lets the UI suppress duplicate "Обнаружил новый экран"
            # log lines when the agent re-visits a screen.
            "is_new": event.is_new,
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


# PER-XX: worker-token-protected RAG query endpoint.
#
# /api/admin/knowledge/query is admin-only by design. ScenarioRunner
# (PER-37) needs run-time RAG to verify expected_result against linked
# spec documents — but it has only a worker token, not an admin JWT.
# Expose the same vector lookup under /api/internal/runs/knowledge-query.
@router.post("/knowledge-query", dependencies=[Depends(require_worker_token)])
async def worker_knowledge_query(
    payload: dict,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Top-K similar chunks for the worker's RAG verification.

    Body: ``{"query": str, "top_k": int = 5, "document_ids": list[UUID]}``.
    Returns ``{"matches": [{...}, ...]}`` — same shape as the admin
    endpoint, minus the LLM answer (worker only needs the chunks).
    """
    from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
    from app.services.embedding import EmbeddingClient

    query_text = (payload.get("query") or "").strip()
    if not query_text:
        raise HTTPException(status_code=422, detail="query is required")
    top_k = int(payload.get("top_k") or 5)
    doc_ids = payload.get("document_ids") or None

    embedder = EmbeddingClient()
    try:
        result = await embedder.embed([query_text])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    distance = KnowledgeChunk.embedding.cosine_distance(
        result.vectors[0]
    ).label("distance")
    stmt = (
        select(
            KnowledgeChunk.id, KnowledgeChunk.document_id,
            KnowledgeChunk.chunk_idx, KnowledgeChunk.text,
            KnowledgeDocument.title, distance,
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
    )
    if doc_ids:
        from uuid import UUID as _UUID
        stmt = stmt.where(KnowledgeChunk.document_id.in_([_UUID(d) for d in doc_ids]))
    stmt = stmt.order_by(distance).limit(top_k)
    rows = (await session.execute(stmt)).all()
    return {
        "embedding_model": result.model_name,
        "matches": [
            {
                "chunk_id": str(r.id),
                "document_id": str(r.document_id),
                "chunk_idx": r.chunk_idx,
                "text": r.text,
                "document_title": r.title,
                "distance": float(r.distance),
            }
            for r in rows
        ],
    }
