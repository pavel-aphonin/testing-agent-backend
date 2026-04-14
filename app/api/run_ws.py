"""/ws/runs/{id} — live progress stream for a single run.

Browsers cannot set arbitrary HTTP headers when opening a WebSocket, so
the JWT is passed via the `?token=` query parameter. We validate it
manually using the same JWT strategy that fastapi-users uses for the
HTTP routes, then check that the user has permission to view this run.

After auth, we:
    1. Send a "snapshot" event with the current state (status + existing
       screens + existing edges) so the client paints something useful
       even before any new events arrive.
    2. Subscribe to the run's Redis channel.
    3. Forward each event to the WebSocket as a JSON text frame.
    4. Clean up on disconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ws import resolve_user_from_token
from app.db import async_session_maker
from app.models.run import Edge, Run, Screen
from app.models.user import UserRole
from app.redis_bus import subscribe_run_events

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)


async def _build_snapshot(session: AsyncSession, run: Run) -> dict:
    """Pull current screens + edges + stats so the client has a starting frame."""
    screens_q = await session.execute(
        select(Screen).where(Screen.run_id == run.id).order_by(Screen.first_seen_at.asc())
    )
    edges_q = await session.execute(
        select(Edge).where(Edge.run_id == run.id).order_by(Edge.step_idx.asc())
    )
    return {
        "type": "snapshot",
        "run": {
            "id": str(run.id),
            "status": run.status,
            "title": run.title,
            "bundle_id": run.bundle_id,
            "mode": run.mode,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error_message": run.error_message,
            "stats": run.stats_json,
        },
        "screens": [
            {
                "id": s.id,
                "screen_id_hash": s.screen_id_hash,
                "name": s.name,
                "visit_count": s.visit_count,
                "screenshot_path": s.screenshot_path,
                "first_seen_at": s.first_seen_at.isoformat() if s.first_seen_at else None,
            }
            for s in screens_q.scalars().all()
        ],
        "edges": [
            {
                "id": e.id,
                "source_screen_hash": e.source_screen_hash,
                "target_screen_hash": e.target_screen_hash,
                "action_type": e.action_type,
                "action_details": e.action_details_json,
                "step_idx": e.step_idx,
                "success": e.success,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in edges_q.scalars().all()
        ],
    }


@router.websocket("/ws/runs/{run_id}")
async def run_progress_ws(
    websocket: WebSocket,
    run_id: UUID,
    token: str = Query(...),
):
    # --- Authenticate the JWT before accepting the upgrade ---
    user = await resolve_user_from_token(token)
    if user is None or not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    # --- Permission + snapshot in one DB session ---
    async with async_session_maker() as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Run not found")
            return
        if user.role != UserRole.ADMIN.value and run.user_id != user.id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
            return

        snapshot = await _build_snapshot(session, run)

    await websocket.accept()
    await websocket.send_text(json.dumps(snapshot, default=str))

    # --- Forward Redis events until the client disconnects ---
    try:
        async for event in subscribe_run_events(str(run_id)):
            try:
                await websocket.send_text(json.dumps(event, default=str))
            except (WebSocketDisconnect, asyncio.CancelledError):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Run WS forwarding failed for run %s", run_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
