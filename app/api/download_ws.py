"""/ws/admin/downloads/{download_id} — HF download progress stream.

Parallel to ``run_ws.py`` but for HF model downloads. The admin's
browser opens this socket right after POSTing ``/api/admin/models/hf/download``
and receives a series of events:

    {"type": "download_started", ...}
    {"type": "progress", "downloaded": 1234567, "total": 89012345, ...}
    ... many progress events ...
    {"type": "download_complete", "model_id": "..."}
    # OR
    {"type": "download_failed", "error": "..."}

The socket closes after the terminal event. If the client disconnects
in the middle of a download, the background task keeps running — the
download completes silently and the admin can still see the new model
in the AdminModels table on the next page refresh.

Only admins may open this socket. Auth uses the same JWT-via-query-param
scheme as ``run_ws.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.auth.ws import resolve_user_from_token
from app.models.user import UserRole
from app.redis_bus import channel_for_download, subscribe_events

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)


_TERMINAL_EVENT_TYPES = {"download_complete", "download_failed"}


@router.websocket("/ws/admin/downloads/{download_id}")
async def download_progress_ws(
    websocket: WebSocket,
    download_id: UUID,
    token: str = Query(...),
):
    # --- Auth before accepting the upgrade ---
    user = await resolve_user_from_token(token)
    if user is None or not user.is_active:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token"
        )
        return
    if user.role != UserRole.ADMIN.value:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Admin only"
        )
        return

    await websocket.accept()

    channel = channel_for_download(str(download_id))
    try:
        async for event in subscribe_events(channel):
            try:
                await websocket.send_text(json.dumps(event, default=str))
            except (WebSocketDisconnect, asyncio.CancelledError):
                break
            if event.get("type") in _TERMINAL_EVENT_TYPES:
                # Clean close after terminal event so the client can
                # tell "done" from "connection dropped".
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "Download WS forwarding failed for download %s", download_id
        )
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
