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


# ── AlfaGen Sandbox ──────────────────────────────────────────────────────────
#
# AlfaGen Sandbox is a corporate OpenAI-compatible LLM service. We proxy
# chat completions, file upload, and tokenization through our backend so
# the installation's token never leaves the server.
#
# Headers required on every request:
#   Authorization: Bearer <uuid-token>
#   systemId:      sanduser
#   messageId:     <uuid per request>
#
# Base paths:
#   GET  /internal/llm/v1/models
#   POST /internal/llm/v1/chat/completions
#   POST /internal/llm/v1/upload-file
#   GET  /internal/llm/v1/upload-file/{taskId}/sse  (SSE)
#   POST /internal/v1/tokenizer/tokens/count
#
# ─────────────────────────────────────────────────────────────────────────────


def _alfagen_headers(settings: dict) -> dict[str, str]:
    import uuid as _uuid
    return {
        "Authorization": f"Bearer {settings.get('api_token') or ''}",
        "systemId": settings.get("system_id") or "sanduser",
        "messageId": str(_uuid.uuid4()),
    }


async def alfagen_chat(
    settings: dict,
    *,
    messages: list[dict],
    model: str | None = None,
    **extra,
) -> dict:
    """Thin wrapper around /internal/llm/v1/chat/completions. Returns
    the parsed JSON response unchanged."""
    url = (settings.get("api_url") or "").rstrip("/")
    if not url or not settings.get("api_token"):
        raise RuntimeError("AlfaGen: не заполнены api_url / api_token")

    body = {
        "model": model or settings.get("default_model") or "Qwen/QwQ-32B",
        "messages": messages,
        "n": 1,
    }
    body.update(extra)
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{url}/internal/llm/v1/chat/completions",
            headers={**_alfagen_headers(settings), "Content-Type": "application/json"},
            json=body,
        )
    if r.status_code >= 400:
        raise RuntimeError(f"AlfaGen {r.status_code}: {r.text[:500]}")
    return r.json()


async def alfagen_enrich_defect(installation: AppInstallation, payload: dict) -> None:
    """On defect.created, ask AlfaGen to rewrite the description to a
    QA standard (steps / expected / actual). Writes the result to the
    defect's ``llm_analysis_json.alfagen_enriched`` field.

    Gated by the ``enable_defect_enrichment`` setting and the
    ``auto_enrich_priorities`` list.
    """
    s = installation.settings or {}
    if not s.get("enable_defect_enrichment"):
        return

    wanted = [p.strip() for p in (s.get("auto_enrich_priorities") or "").split(",") if p.strip()]
    if wanted and payload.get("priority") not in wanted:
        return

    system_prompt = s.get("enrichment_prompt") or (
        "Ты — senior QA-инженер. Переформулируй описание найденного дефекта "
        "в соответствии с ГОСТ: краткий заголовок, шаги воспроизведения, "
        "ожидаемый результат, фактический результат."
    )
    user_prompt = (
        f"Экран: {payload.get('screen_name') or '—'}\n"
        f"Приоритет: {payload.get('priority')}\n"
        f"Тип: {payload.get('kind') or '—'}\n"
        f"Исходный заголовок: {payload.get('title') or '—'}\n"
        f"Исходное описание:\n{payload.get('description') or '—'}"
    )

    try:
        resp = await alfagen_chat(
            s,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        enriched = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("AlfaGen enrichment failed for %s: %s", payload.get("defect_id"), exc)
        return

    # Persist to the defect row
    from uuid import UUID as _UUID

    from app.db import async_session_maker
    from app.models.defect import DefectModel

    try:
        async with async_session_maker() as session:
            defect = await session.get(DefectModel, _UUID(str(payload.get("defect_id"))))
            if defect is None:
                return
            data = dict(defect.llm_analysis_json or {})
            data["alfagen_enriched"] = {
                "text": enriched,
                "model": s.get("default_model"),
                "enriched_at_installation": str(installation.id),
            }
            defect.llm_analysis_json = data
            await session.commit()
            logger.info(
                "AlfaGen enriched defect %s (%d chars)",
                payload.get("defect_id"), len(enriched),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Writing AlfaGen enrichment to DB failed")


# ── Registry ─────────────────────────────────────────────────────────────────

BUILTINS: dict[str, HandlerFn] = {
    "jira.create_issue": jira_create_issue,
    "alfagen.enrich_defect": alfagen_enrich_defect,
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
