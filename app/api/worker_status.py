"""/api/worker/status — public endpoint for the UI's "Connected" indicator.

Status comes from a Redis key the worker updates on every heartbeat and on
every event post. Considered "connected" if the timestamp is within
HEARTBEAT_FRESHNESS_SEC of now. We don't track per-worker IDs because the
demo assumes a single worker — the indicator is "is anyone alive?", not
"how many workers".

There's also a worker-token POST that the worker uses to push its heartbeat.
Heartbeats are best-effort (Redis down ≠ worker down), so the UI shows
"unknown" rather than "disconnected" if the heartbeat key is missing.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.internal_runs import require_worker_token
from app.redis_bus import get_redis

# How fresh a heartbeat must be to count as "connected". Worker pings
# every 5 sec; we allow 3x that to survive transient glitches.
HEARTBEAT_FRESHNESS_SEC = 15
HEARTBEAT_KEY = "worker:heartbeat"

public_router = APIRouter(prefix="/api/worker", tags=["worker"])
internal_router = APIRouter(prefix="/api/internal/worker", tags=["worker", "internal"])


@public_router.get("/status")
async def get_worker_status() -> dict:
    """Return the worker's connection status.

    States:
    - connected: heartbeat within HEARTBEAT_FRESHNESS_SEC
    - stale: heartbeat older than HEARTBEAT_FRESHNESS_SEC (worker may be busy)
    - unknown: no heartbeat ever / Redis unavailable
    """
    try:
        redis = await get_redis()
        raw = await redis.get(HEARTBEAT_KEY)
    except Exception:
        return {"status": "unknown", "last_heartbeat_ago_sec": None}

    if raw is None:
        return {"status": "unknown", "last_heartbeat_ago_sec": None}

    try:
        last_ts = float(raw)
    except (TypeError, ValueError):
        return {"status": "unknown", "last_heartbeat_ago_sec": None}

    age = time.time() - last_ts
    if age <= HEARTBEAT_FRESHNESS_SEC:
        state = "connected"
    else:
        state = "stale"
    return {"status": state, "last_heartbeat_ago_sec": round(age, 1)}


@internal_router.post(
    "/heartbeat",
    dependencies=[Depends(require_worker_token)],
)
async def post_heartbeat() -> dict:
    """Worker pings this every few seconds so the UI knows it's alive."""
    try:
        redis = await get_redis()
        await redis.set(HEARTBEAT_KEY, str(time.time()), ex=60)
        return {"ok": True}
    except Exception:
        # Heartbeats are best-effort; never fail the worker because Redis is down.
        return {"ok": False}
