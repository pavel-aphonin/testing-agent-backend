"""PER-177: per-IP rate limit for /auth/jwt/login.

Why a custom middleware instead of slowapi:
    Single-instance deploy + small fixed surface (one endpoint). Pulling
    in slowapi to gate one path would add a dependency we don't need
    elsewhere. The bucket logic below is ~30 lines and trivially
    testable. If we ever shard the backend we'll switch to a Redis-backed
    rate limiter and keep the same middleware boundary.

Algorithm:
    Sliding window per source IP. Each request to the protected path
    records a timestamp; on every request older entries are evicted
    (anything outside the window). If the window already holds ``max``
    entries, the request is rejected with 429.

Bypass / safety:
    * Only the configured paths are checked — everything else passes
      through with zero overhead.
    * The window dict can grow unbounded in adversarial traffic, but
      each IP's list is capped at ``max`` items (we evict on every
      request), so the worst case is one short list per attacker IP.
      A janitor task is overkill for the expected traffic profile.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle login attempts per source IP.

    Default 10 attempts per 60s window — generous enough for a human
    fat-fingering a password three times in a row, tight enough that
    a 10-character-alphabet dictionary brute force takes hours instead
    of minutes.
    """

    def __init__(
        self,
        app,
        *,
        path: str = "/auth/jwt/login",
        max_per_window: int = 10,
        window_sec: float = 60.0,
    ) -> None:
        super().__init__(app)
        self._path = path
        self._max = max_per_window
        self._window = window_sec
        self._hits: defaultdict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Cheap path check first — most requests aren't login.
        if request.url.path != self._path:
            return await call_next(request)

        ip = (request.client.host if request.client else "") or "unknown"
        now = time.monotonic()
        bucket = self._hits[ip]
        # Evict expired entries (anything past the window).
        cutoff = now - self._window
        if bucket and bucket[0] < cutoff:
            self._hits[ip] = [t for t in bucket if t >= cutoff]
            bucket = self._hits[ip]
        if len(bucket) >= self._max:
            # Surface a clean 429 + Retry-After so well-behaved clients
            # (browsers, mobile apps) back off automatically rather than
            # spamming on user re-clicks.
            oldest = bucket[0]
            retry_after = max(1, int(self._window - (now - oldest)) + 1)
            logger.warning(
                "Login rate limit hit: ip=%s attempts=%d window=%ss",
                ip, len(bucket), self._window,
            )
            return JSONResponse(
                {
                    "detail": (
                        "Слишком много попыток входа. Попробуйте через "
                        f"{retry_after} с."
                    )
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        # Record this attempt — even if downstream login fails the
        # attacker spends one slot per try. Successful login also
        # counts but that's fine; the limit is generous.
        bucket.append(now)
        return await call_next(request)
