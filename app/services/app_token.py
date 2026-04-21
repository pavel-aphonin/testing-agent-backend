"""Short-lived installation tokens.

An installation token is a JWT scoped to a single (workspace, installation,
user) triple. It's meant to be handed to an app's iframe so the app can
call the Марков API on the user's behalf WITHOUT learning the user's
long-lived auth token.

Claims:
    sub:             user_id
    wsid:            workspace_id
    inst:            installation_id
    aud:             "app-installation"
    perms:           list[str] — subset of user.permissions intersected
                     with the manifest.permissions_required declaration
    exp:             1 hour from issue

The same JWT secret as the main auth backend is used; the token is
accepted by a dedicated dependency that validates the `aud` claim.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

AUDIENCE = "app-installation"
TOKEN_TTL = timedelta(hours=1)

_bearer_scheme = HTTPBearer(auto_error=False)


def issue_installation_token(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    granted_permissions: list[str],
) -> tuple[str, datetime]:
    """Build a signed JWT for an app iframe. Returns (token, expires_at)."""
    now = datetime.now(timezone.utc)
    exp = now + TOKEN_TTL
    payload = {
        "sub": str(user_id),
        "wsid": str(workspace_id),
        "inst": str(installation_id),
        "aud": AUDIENCE,
        "perms": sorted(set(granted_permissions)),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, exp


def decode_installation_token(token: str) -> dict:
    """Validate + decode an installation token. Raises 401 on failure."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid installation token: {e}",
        ) from e


async def require_installation_token(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> dict:
    """FastAPI dependency — extracts + validates the token from the
    Authorization header. Use on app-facing proxy endpoints."""
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Installation token required")
    return decode_installation_token(creds.credentials)
