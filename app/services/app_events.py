"""App event emitter + webhook delivery.

Call ``emit_event("defect.created", payload, workspace_id=...)`` from any
backend handler. We enumerate installations in that workspace whose
manifest.hooks contain a matching ``event``, then fire-and-forget a
background task per subscribed installation to POST the payload to
``installation.settings.webhook_url``.

Result of each delivery is recorded in ``app_event_deliveries``.

Security: events always carry a signed HMAC header ``X-Markov-Signature``
so the receiving app can verify authenticity. The shared secret is the
jwt_secret (same as auth — avoids adding another config knob).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import async_session_maker
from app.models.app_event import AppEventDelivery
from app.models.app_package import AppInstallation, AppPackageVersion

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 10.0  # seconds


def _sign(body: bytes) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


async def emit_event(
    event: str,
    payload: dict,
    *,
    workspace_id: UUID | None = None,
) -> None:
    """Fire the event to all subscribed installations. Non-blocking —
    schedules delivery tasks and returns immediately."""
    async with async_session_maker() as session:
        q = select(AppInstallation, AppPackageVersion).join(
            AppPackageVersion, AppPackageVersion.id == AppInstallation.version_id
        ).where(AppInstallation.is_enabled.is_(True))
        if workspace_id is not None:
            q = q.where(AppInstallation.workspace_id == workspace_id)
        result = await session.execute(q)

        to_deliver: list[tuple[AppInstallation, str]] = []
        for inst, ver in result.all():
            hooks = (ver.manifest or {}).get("hooks") or []
            for h in hooks:
                if h.get("event") == event:
                    webhook_url = (inst.settings or {}).get("webhook_url")
                    if webhook_url:
                        to_deliver.append((inst, webhook_url))
                        break

        for inst, url in to_deliver:
            delivery = AppEventDelivery(
                installation_id=inst.id,
                event=event,
                payload=payload,
            )
            session.add(delivery)
            await session.flush()
            # Schedule the actual POST without blocking the commit.
            asyncio.create_task(_deliver(delivery.id, url, event, payload))
        await session.commit()


async def _deliver(delivery_id: UUID, url: str, event: str, payload: dict) -> None:
    body = json.dumps({"event": event, "payload": payload}, default=str).encode()
    signature = _sign(body)
    async with async_session_maker() as session:
        d = await session.get(AppEventDelivery, delivery_id)
        if d is None:
            return
        d.attempts += 1
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Markov-Event": event,
                        "X-Markov-Signature": signature,
                    },
                )
                d.response_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    d.status = "delivered"
                    d.delivered_at = datetime.now(timezone.utc)
                    d.last_error = None
                else:
                    d.status = "failed"
                    d.last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
        except Exception as exc:  # noqa: BLE001
            d.status = "failed"
            d.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("App event delivery failed: %s", exc)
        await session.commit()
