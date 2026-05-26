"""PER-183: lazy Sentry init.

Why a separate module and not a one-liner in ``main.py``:
    * The DSN-empty branch is the **common** case (dev, on-prem with
      no Sentry org). Wrapping the call in an explicit ``init_sentry``
      function keeps the logging — "Sentry disabled" vs "Sentry init
      for <env> @ <release>" — in one place instead of scattered.
    * The FastAPI integration is the only one we ship today, but the
      function signature is generic so adding Celery / Redis / etc.
      later doesn't change callers.
    * Tests can monkeypatch ``init_sentry`` to a no-op without having
      to mock the SDK itself.

The SDK is imported at module-import time so a broken install fails
loudly at boot rather than silently swallowing exceptions because
``import sentry_sdk`` failed deep inside a request handler.
"""

from __future__ import annotations

import logging

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

logger = logging.getLogger(__name__)

_initialised = False


def init_sentry(
    dsn: str,
    *,
    environment: str = "dev",
    release: str = "",
    traces_sample_rate: float = 0.0,
) -> bool:
    """Start the Sentry SDK if ``dsn`` is non-empty. No-op otherwise.

    Returns ``True`` when the SDK was actually started, ``False``
    otherwise — callers can use this to log a one-liner so operators
    notice "Sentry isn't on" without trawling startup logs.

    Idempotent: a second call with the same args is a no-op (the SDK
    itself would tolerate re-init but we'd double-log the "Sentry
    enabled" line, which is noisy in tests that import main several
    times).
    """
    global _initialised
    if _initialised:
        return True

    if not dsn or not dsn.strip():
        logger.info(
            "Sentry disabled (SENTRY_DSN not set). Unhandled exceptions "
            "will still hit the logs but won't be tracked centrally.",
        )
        return False

    # ``LoggingIntegration`` ships any ``logger.error(...)`` (and worse)
    # as a Sentry event automatically — saves having to scatter
    # ``capture_exception`` calls across the codebase. ``event_level``
    # sits at ERROR so warnings stay informational; bump to WARNING in
    # an env where warnings indicate something the on-call should see.
    sentry_logging = LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR,
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,  # ``""`` would tag releases as the empty string
        traces_sample_rate=traces_sample_rate,
        # FastAPI integration is auto-detected from the installed
        # extras, so we don't need to list it explicitly. Asyncio
        # integration links context across ``await`` boundaries so
        # the request that produced an exception still carries
        # through into the Sentry event.
        integrations=[sentry_logging, AsyncioIntegration()],
        # Don't try to be clever about PII — leave the default
        # (``send_default_pii=False``) so we never leak request
        # bodies / headers until the PII-scrubbing follow-up lands.
        send_default_pii=False,
    )
    _initialised = True
    logger.info(
        "Sentry enabled (env=%s, release=%s, traces_sample_rate=%s)",
        environment,
        release or "<unset>",
        traces_sample_rate,
    )
    return True
