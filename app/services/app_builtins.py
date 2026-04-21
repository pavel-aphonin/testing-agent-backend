"""Built-in app handlers.

The extension system supports two execution models:

1. **Webhook** — manifest.hooks[].handler is any string, installation.settings
   has ``webhook_url``; we POST the event payload to that URL.
2. **Built-in** — manifest.hooks[].handler starts with ``builtin:`` (e.g.
   ``builtin:jira.create_issue``). We look up the handler in this
   registry and call it in-process. No webhook_url needed.

Built-ins are trusted code we ship; they have direct API access so we
can implement tight integrations (Jira, AlfaGen) without running user
Python in the backend.

Each handler receives the installation row and the event payload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import httpx

from app.models.app_package import AppInstallation

logger = logging.getLogger(__name__)

HandlerFn = Callable[[AppInstallation, dict], Awaitable[None]]


# ── Jira ─────────────────────────────────────────────────────────────────────

def _jira_auth_headers(settings: dict) -> dict[str, str]:
    """Basic-auth header for Jira Cloud (email + API token)."""
    import base64

    email = settings.get("api_email") or ""
    token = settings.get("api_token") or ""
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _map_priority(markov_priority: str, settings: dict) -> str:
    """Convert Markov P0..P3 to the Jira priority name per settings."""
    raw = settings.get("priorities") or ""
    try:
        mapping = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        mapping = {}
    default_map = {"P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low"}
    return mapping.get(markov_priority) or default_map.get(markov_priority, "Medium")


async def jira_create_issue(installation: AppInstallation, payload: dict) -> None:
    """Handle defect.created — optionally POST to Jira."""
    s = installation.settings or {}
    url = (s.get("jira_url") or "").rstrip("/")
    project = s.get("project_key")
    issue_type = s.get("default_issue_type") or "Bug"
    auto_prios = [
        p.strip() for p in (s.get("auto_create_priorities") or "").split(",") if p.strip()
    ]

    if not url or not project or not s.get("api_token") or not s.get("api_email"):
        logger.info("Jira integration for %s not fully configured; skipping", installation.id)
        return
    if auto_prios and payload.get("priority") not in auto_prios:
        logger.info(
            "Defect priority %s not in auto_create_priorities %s; skipping",
            payload.get("priority"), auto_prios,
        )
        return

    body = {
        "fields": {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": f"[Markov] {payload.get('title') or 'Defect'}",
            "description": _build_description(payload),
            "priority": {"name": _map_priority(payload.get("priority", "P2"), s)},
        }
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{url}/rest/api/2/issue",
            headers=_jira_auth_headers(s),
            json=body,
        )
    if resp.status_code >= 400:
        logger.warning(
            "Jira create_issue failed for installation %s: %s %s",
            installation.id, resp.status_code, resp.text[:500],
        )
    else:
        data = resp.json()
        logger.info("Jira issue %s created for defect %s", data.get("key"), payload.get("defect_id"))


def _build_description(payload: dict) -> str:
    parts = []
    if payload.get("screen_name"):
        parts.append(f"Экран: {payload['screen_name']}")
    if payload.get("kind"):
        parts.append(f"Тип: {payload['kind']}")
    if payload.get("description"):
        parts.append("")
        parts.append(payload["description"])
    parts.append("")
    parts.append(f"Run ID: {payload.get('run_id')}")
    parts.append(f"Defect ID: {payload.get('defect_id')}")
    parts.append("")
    parts.append("_Создано автоматически интеграцией Markov → Jira._")
    return "\n".join(parts)


# ── AlfaGen ──────────────────────────────────────────────────────────────────

async def alfagen_send_defect(installation: AppInstallation, payload: dict) -> None:
    """Push a defect to AlfaGen sandbox via its REST API.

    The actual AlfaGen endpoint contract will be filled in once we have
    the concrete API reference. This stub implements the configured
    POST to ``api_url`` with a bearer token.
    """
    s = installation.settings or {}
    url = (s.get("api_url") or "").rstrip("/")
    token = s.get("api_token")
    if not url or not token:
        logger.info("AlfaGen integration for %s not fully configured; skipping", installation.id)
        return

    body = {
        "source": "markov",
        "defect_id": payload.get("defect_id"),
        "run_id": payload.get("run_id"),
        "priority": payload.get("priority"),
        "title": payload.get("title"),
        "description": payload.get("description"),
        "screen": payload.get("screen_name"),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{url}/api/v1/defects",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if resp.status_code >= 400:
        logger.warning(
            "AlfaGen push failed for installation %s: %s %s",
            installation.id, resp.status_code, resp.text[:500],
        )


# ── Registry ─────────────────────────────────────────────────────────────────

BUILTINS: dict[str, HandlerFn] = {
    "jira.create_issue": jira_create_issue,
    "alfagen.send_defect": alfagen_send_defect,
}


async def dispatch_builtin(name: str, installation: AppInstallation, payload: dict) -> None:
    fn = BUILTINS.get(name)
    if fn is None:
        logger.warning("Unknown builtin handler: %s", name)
        return
    try:
        await fn(installation, payload)
    except Exception:  # noqa: BLE001
        logger.exception("Builtin handler %s failed", name)
