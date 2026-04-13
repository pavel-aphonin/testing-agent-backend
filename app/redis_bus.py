"""Redis pub/sub helpers for live progress streams.

The original use case was run progress (worker → backend → Redis →
WebSocket → browser). Block 3 added a second one for HF model downloads
(background task → Redis → admin WebSocket → browser) with exactly the
same shape, so this module now exposes generic ``publish_event`` /
``subscribe_events`` helpers plus thin run-specific wrappers for the
original caller.

The flow for runs:
    1. Worker posts an event to /api/internal/runs/{id}/event
    2. The endpoint writes to Postgres (Run/Screen/Edge tables)
    3. The endpoint publishes the same event to channel "run:{id}:events"
    4. The /ws/runs/{id} WebSocket endpoint subscribes to that channel
       for the duration of a browser connection and forwards each
       message to the client as soon as it arrives.

The flow for downloads is symmetrical — see ``hf_downloader`` and
``download_ws``.

Channel naming is caller-controlled. Whoever publishes decides whether a
channel is per-run, per-download, per-user, etc. The subscribing
WebSocket is responsible for verifying that the connecting user has
permission to see the contents of that channel before subscribing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from app.config import settings


def channel_for_run(run_id: str) -> str:
    return f"run:{run_id}:events"


def channel_for_download(download_id: str) -> str:
    return f"download:{download_id}:events"


_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Module-level singleton. The connection pool handles concurrency."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


# --- generic helpers ---------------------------------------------------------


async def publish_event(channel: str, event: dict[str, Any]) -> None:
    """JSON-encode an event and publish it to an arbitrary Redis channel.

    ``default=str`` lets us pass datetimes/UUIDs without per-caller
    serialization code — they render as ISO strings / hex strings.
    """
    payload = json.dumps(event, default=str)
    redis = get_redis()
    await redis.publish(channel, payload)


async def subscribe_events(channel: str) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded events from a channel until the caller breaks out.

    The caller is responsible for breaking out of the iteration when the
    WebSocket disconnects. The Redis pubsub object is closed in a
    ``finally`` block to avoid leaking subscriptions. Malformed JSON is
    skipped rather than raising, so a single bad event can't break the
    stream for well-behaved clients.
    """
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message is None:
                continue
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not data:
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                # Malformed event — skip rather than break the stream.
                continue
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


# --- run-specific convenience wrappers --------------------------------------
#
# Kept as thin wrappers so existing callers (internal_runs.py, run_ws.py)
# don't need to learn the channel naming convention.


async def publish_run_event(run_id: str, event: dict[str, Any]) -> None:
    await publish_event(channel_for_run(run_id), event)


async def subscribe_run_events(run_id: str) -> AsyncIterator[dict[str, Any]]:
    async for event in subscribe_events(channel_for_run(run_id)):
        yield event
