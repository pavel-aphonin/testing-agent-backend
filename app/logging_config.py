"""Centralised logging setup (PER-118).

Two formatters, selected by the ``LOGGING_BACKEND`` env var:

* ``none`` (default) — preserve the legacy plain-text format the
  developer expects when ``docker logs ta-backend`` is open in a
  terminal. Nothing about the dev loop changes.

* anything else (``elasticsearch`` / ``external``) — emit one
  JSON object per line on stdout. The schema includes the standard
  ``timestamp / level / logger / message`` plus stable enrichment
  fields (``service``, plus whatever the LogRecord carries in
  ``extra=…``). Filebeat / a customer log shipper picks these up
  unmodified.

Centralising the setup here lets uvicorn + alembic + our own
loggers share the same handler, so a ``run_id`` set in extra=… on
one log shows up the same way as on another.
"""

from __future__ import annotations

import logging
import os
import sys

from pythonjsonlogger import jsonlogger


# Identifies the service in shared log streams. ``backend`` matches
# the ``markov.service`` label set in docker-compose.yml so Kibana's
# data view groups the indices correctly.
_SERVICE_NAME = "backend"


class _ServiceFilter(logging.Filter):
    """Inject ``service`` into every record so we don't have to
    remember to pass it via ``extra=`` at every call site."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.service = _SERVICE_NAME
        return True


def setup_logging() -> None:
    """Install handlers + formatter on the root logger.

    Idempotent — safe to call twice (uvicorn's reloader calls main
    multiple times during a reload cycle, and we don't want stacked
    handlers leaking duplicate lines).
    """
    backend = (os.environ.get("LOGGING_BACKEND") or "none").strip().lower()
    json_mode = backend not in ("", "none")

    root = logging.getLogger()
    # Clear handlers from any previous configuration (uvicorn-reload,
    # repeated imports under tests, etc.).
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ServiceFilter())

    if json_mode:
        # ``rename_fields`` aligns our field names with the ECS-ish
        # convention Kibana parses out of the box (``@timestamp``,
        # ``log.level``). ``json_ensure_ascii=False`` keeps Russian
        # log lines readable in raw stream views.
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(service)s",
            rename_fields={
                "asctime": "@timestamp",
                "levelname": "log.level",
                "name": "logger",
            },
            json_ensure_ascii=False,
        )
    else:
        # Same format uvicorn shows by default — no surprises in
        # ``docker logs`` when LOGGING_BACKEND=none.
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    # Quiet the noisiest 3rd-party loggers down to WARNING — they
    # otherwise spam the Kibana index with every httpcore handshake.
    for name in ("httpcore", "httpx", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)
