"""JWT resolution for WebSocket endpoints.

Browsers can't set arbitrary Authorization headers when opening a
WebSocket, so our WS endpoints accept the JWT via a ``?token=`` query
parameter and decode it manually with the same strategy fastapi-users
uses for HTTP routes.

Extracted from ``app/api/run_ws.py`` so that the HF download
WebSocket (``app/api/download_ws.py``) can reuse the exact same logic
without importing from another endpoint module.
"""

from __future__ import annotations

from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from app.auth.users import UserManager, get_jwt_strategy
from app.db import async_session_maker
from app.models.user import User


async def resolve_user_from_token(token: str) -> User | None:
    """Decode a JWT from a ``?token=`` param and return the User row.

    Returns ``None`` on any auth failure (bad signature, expired token,
    unknown user) — callers are expected to close the WebSocket with
    ``WS_1008_POLICY_VIOLATION`` in that case.

    ``JWTStrategy.read_token`` expects a ``UserManager`` (which exposes
    ``parse_id`` and ``get``), not a raw ``SQLAlchemyUserDatabase``.
    Passing the latter raises:

        AttributeError: 'SQLAlchemyUserDatabase' object has no attribute 'parse_id'

    This signature matches the old private ``_resolve_user`` in
    ``run_ws.py`` so callers migrating over don't have to change any
    call sites.
    """
    strategy = get_jwt_strategy()
    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)
        user = await strategy.read_token(token, user_manager)
        return user
