"""PER-180: typed error codes + i18n exception handler.

Why this exists
---------------
The codebase grew ~200 ad-hoc ``HTTPException(detail="...")`` calls
mixing ru/en wording and occasionally interpolating model IDs or table
names into the user-visible string. Two problems:

1. Sensitive data (UUIDs from dependency checks, "FK from
   table_x" snippets) leaks into the SPA's toast banner.
2. The UI is bilingual — but the detail strings aren't, so half the
   error toasts are in the wrong language for the operator.

How this fixes it
-----------------
A small catalog of ``ErrorCode`` values, each backed by a ru/en
message template. Endpoints raise ``HTTPException`` with the **code**
as detail, optionally attaching template variables via the
``X-Error-Params`` header. The handler:

    * Reads ``Accept-Language`` (or the override header).
    * Substitutes template variables.
    * Returns ``{"detail": "<localized text>", "code": "...",
      "params": {...}}``.

Backward compat: any HTTPException whose detail is **not** a known
ErrorCode passes through unchanged. So this module ships as
infrastructure + a handful of pilot endpoint migrations; the rest
move over as we touch them.

500 handler
-----------
The same module registers a global handler that maps any uncaught
exception to ``{"detail": "<localized 'something went wrong' text>",
"code": "INTERNAL"}`` so future code paths can't accidentally leak a
stack trace or ORM detail into the response body. The full exception
still hits the logs and (when enabled) Sentry via PER-183's
``LoggingIntegration``.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    """Stable, machine-readable error identifiers.

    Values are uppercase snake-case strings so they're equally
    comfortable as detail strings, log keys, and frontend i18n keys
    once we get there. New codes are append-only — renaming a code
    breaks any frontend that switched on it.
    """

    # 4xx — workspace lifecycle (pilot, paired with PER-186)
    WORKSPACE_HAS_ACTIVE_RUNS = "WORKSPACE_HAS_ACTIVE_RUNS"

    # 4xx — auth / authorization (pilot)
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # 4xx — validation
    INVALID_INPUT = "INVALID_INPUT"

    # 5xx — generic fallback (never shown verbatim, used by the
    # unhandled-exception handler to map any Python error to a stable
    # code).
    INTERNAL = "INTERNAL"


# Message templates. Variables in ``{braces}`` are interpolated from
# the ``params`` dict supplied via the ``X-Error-Params`` header (JSON
# encoded) or directly when the endpoint constructs the response.
#
# Default to the Russian copy because the product UI defaults to ru;
# English is the explicit fallback when the operator sets
# ``Accept-Language: en``.
_MESSAGES: dict[ErrorCode, dict[str, str]] = {
    ErrorCode.WORKSPACE_HAS_ACTIVE_RUNS: {
        "ru": (
            "В этом workspace ещё активны {count} запуск(ов). "
            "Дождитесь завершения или повторите с "
            "?cancel_runs=true для массовой отмены."
        ),
        "en": (
            "This workspace still has {count} active run(s). "
            "Wait for them to finish or retry with "
            "?cancel_runs=true to cancel them all."
        ),
    },
    ErrorCode.NOT_AUTHENTICATED: {
        "ru": "Необходима авторизация.",
        "en": "Authentication required.",
    },
    ErrorCode.PERMISSION_DENIED: {
        "ru": "Недостаточно прав для выполнения операции.",
        "en": "You don't have permission to perform this action.",
    },
    ErrorCode.INVALID_INPUT: {
        "ru": "Введены некорректные данные.",
        "en": "Invalid input.",
    },
    ErrorCode.INTERNAL: {
        "ru": "Внутренняя ошибка. Мы уже знаем о ней.",
        "en": "Internal server error. We've been notified.",
    },
}


def _pick_language(request: Request) -> str:
    """Best-effort language pick from ``Accept-Language``.

    We don't run a full RFC 4647 negotiation here — the SPA only
    ever ships ``ru`` or ``en`` (the only two languages we localize
    for), and any other tag falls back to ru. ``X-Lang`` is honored
    as an override for tooling that can't set Accept-Language
    cleanly (curl + httpie both can, but tests sometimes find it
    easier to set a custom header).
    """
    override = request.headers.get("X-Lang")
    if override:
        lang = override.strip().lower()[:2]
        if lang in ("ru", "en"):
            return lang
    accept = request.headers.get("Accept-Language", "")
    # Match the first tag's primary subtag — Accept-Language is a
    # q-weighted list but we don't have enough languages to need a
    # real parser yet.
    for token in accept.split(","):
        tag = token.split(";")[0].strip().lower()[:2]
        if tag in ("ru", "en"):
            return tag
    return "ru"


def _render(code: ErrorCode, lang: str, params: Mapping[str, Any] | None) -> str:
    template = _MESSAGES.get(code, _MESSAGES[ErrorCode.INTERNAL]).get(lang)
    if template is None:
        template = _MESSAGES[ErrorCode.INTERNAL]["ru"]
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError) as exc:
        # A missing param shouldn't break the response — log so the
        # dev notices, but still ship the raw template so the UI
        # gets *something*.
        logger.warning(
            "errors._render: missing params for %s (lang=%s): %s",
            code,
            lang,
            exc,
        )
        return template


def _is_known_code(detail: Any) -> ErrorCode | None:
    """Recognise either an ``ErrorCode`` member or its string value."""
    if isinstance(detail, ErrorCode):
        return detail
    if isinstance(detail, str):
        try:
            return ErrorCode(detail)
        except ValueError:
            return None
    return None


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Wrap FastAPI's default HTTPException handler with code translation.

    The handler is registered for ``HTTPException`` (covers both
    Starlette's and FastAPI's variants since FastAPI's inherits).
    """
    code = _is_known_code(exc.detail)
    if code is None:
        # Legacy raw-string detail — pass through unchanged so we
        # don't break endpoints that haven't been migrated yet.
        # NOTE: this path is the one that still has the leak risk —
        # migrating to ErrorCode is what closes it.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None) or {},
        )

    lang = _pick_language(request)
    params: dict[str, Any] = {}
    # Endpoints attach template params via the ``X-Error-Params``
    # header so the handler can interpolate without parsing the
    # response body. Bad JSON in the header is ignored — the
    # template's default rendering still happens.
    raw_params = (exc.headers or {}).get("x-error-params") if exc.headers else None
    if raw_params:
        import json
        try:
            parsed = json.loads(raw_params)
            if isinstance(parsed, dict):
                params = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    text = _render(code, lang, params)
    # Strip our own header from what we send back — the client doesn't
    # need to see the raw params again.
    response_headers = dict(exc.headers or {})
    response_headers.pop("x-error-params", None)
    response_headers.pop("X-Error-Params", None)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": text,
            "code": code.value,
            "params": params or None,
        },
        headers=response_headers,
    )


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Map Pydantic validation errors to ``INVALID_INPUT`` so the
    client surfaces a localized banner instead of raw Pydantic JSON.

    The raw ``errors()`` list is still attached under ``params`` so the
    UI can highlight per-field issues when it knows how. Migrated
    forms read the field path from there; unmigrated forms show just
    the localized headline and remain functional.
    """
    lang = _pick_language(request)
    text = _render(ErrorCode.INVALID_INPUT, lang, None)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": text,
            "code": ErrorCode.INVALID_INPUT.value,
            "params": {"errors": exc.errors()},
        },
    )


async def _unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Catch-all for anything that isn't an HTTPException.

    The full traceback hits the logger (and Sentry, via PER-183's
    LoggingIntegration). The client only sees a stable, localized
    ``INTERNAL`` code — never the exception type, never the
    message, never a stack trace.
    """
    logger.exception(
        "Unhandled exception at %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    lang = _pick_language(request)
    text = _render(ErrorCode.INTERNAL, lang, None)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": text,
            "code": ErrorCode.INTERNAL.value,
            "params": None,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire the three handlers into the FastAPI app.

    Idempotent in practice — FastAPI's ``add_exception_handler`` is
    last-write-wins, so re-calling on hot reload replaces with the
    same function. Tests can opt out by simply not calling this.
    """
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


def with_params(**params: Any) -> dict[str, str]:
    """Build the ``headers=`` dict for an HTTPException so the handler
    can interpolate template variables.

    Example:
        raise HTTPException(
            status_code=409,
            detail=ErrorCode.WORKSPACE_HAS_ACTIVE_RUNS,
            headers=with_params(count=active),
        )

    Returns a single-entry dict ``{"X-Error-Params": "<json>"}``;
    callers can ``| with_params(...)`` it into an existing headers
    dict if they're already setting headers (uncommon).
    """
    import json
    return {"X-Error-Params": json.dumps(params, default=str)}
