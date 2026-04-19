"""Live simulator mirror — proxies MJPEG from the worker host into the browser.

The worker (running on the host, not in Docker) spawns SimMirror as a
sidecar process when it claims a real-iOS run. SimMirror serves an
MJPEG stream on port 9999 of the host. Browsers can't reach the host
directly through the docker-compose network, so this endpoint proxies
the stream from `host.docker.internal:9999` through the backend.

Why proxy instead of CORS?
- The browser is already authenticated against the backend with a JWT;
  proxying lets us reuse that auth and gate access by run ownership.
- A CORS-enabled MJPEG endpoint on the worker host would expose the
  simulator video to anyone on the local network, which is fine for
  dev but not for shared dev machines.
- It also lets the backend keep the URL stable (`/api/runs/{id}/mirror`)
  even if we switch capture transports later.

The proxy is a streaming passthrough — we never buffer the whole stream
in memory. httpx async streaming + StreamingResponse handles back-
pressure naturally: if the browser is slow, httpx blocks reading from
the upstream, and SimMirror drops frames at its source.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.run import Run
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _mirror_base_url() -> str:
    """Where SimMirror is running on the worker host.

    Defaults to host.docker.internal because backend lives in docker
    and the host runs the worker + SimMirror natively. On Linux/CUDA
    setups where the worker is also containerized this would be the
    worker service hostname instead.
    """
    return os.environ.get(
        "SIM_MIRROR_URL", "http://host.docker.internal:9999"
    ).rstrip("/")


async def _check_run_visible(
    run_id: UUID, user: User, session: AsyncSession
) -> Run:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if "users.view" not in (user.permissions or []) and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    return run


@router.get("/{run_id}/mirror", response_class=StreamingResponse)
async def stream_mirror(
    run_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> StreamingResponse:
    """MJPEG passthrough of the simulator window for a running run.

    Returns 503 if the SimMirror sidecar is unreachable (run not yet
    claimed by a real-executor worker, or running in synthetic mode).
    """
    await _check_run_visible(run_id, user, session)

    upstream = f"{_mirror_base_url()}/stream.mjpg"

    # Open the upstream connection once and keep it open as long as the
    # browser is connected. We must NOT use `with httpx.AsyncClient()`
    # here because StreamingResponse iterates the body lazily and the
    # context manager would close the connection before the first chunk
    # is sent.
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream_resp = await client.stream("GET", upstream).__aenter__()
    except httpx.ConnectError as exc:
        await client.aclose()
        logger.info("SimMirror unreachable at %s: %s", upstream, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Live mirror is not available. Either the run is not "
                "currently being executed by a real-iOS worker, or the "
                "SimMirror sidecar failed to start. Check that the "
                "worker is running with --executor real on a host "
                "with a booted simulator and that "
                "testing-agent-sim-mirror has been built."
            ),
        )

    if upstream_resp.status_code != 200:
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SimMirror returned {upstream_resp.status_code}",
        )

    content_type = upstream_resp.headers.get(
        "content-type", "multipart/x-mixed-replace; boundary=frame"
    )

    async def relay():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        media_type=content_type,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.get("/{run_id}/mirror/snapshot")
async def mirror_snapshot(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Single most recent JPEG frame. Cheaper than streaming for thumbnails."""
    await _check_run_visible(run_id, user, session)

    upstream = f"{_mirror_base_url()}/snapshot.jpg"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(upstream)
            resp.raise_for_status()
            return StreamingResponse(
                iter([resp.content]),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache"},
            )
    except (httpx.HTTPError, httpx.ConnectError) as exc:
        logger.info("SimMirror snapshot unreachable at %s: %s", upstream, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live mirror snapshot is not available",
        )
