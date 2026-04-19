"""fastapi-users wiring: JWT backend, user manager, permission-based guards."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from app.config import settings
from app.db import async_session_maker
from app.models.user import User

# --- Transport + strategy ----------------------------------------------------

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.jwt_access_token_expires_min * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# --- User database + manager -------------------------------------------------


async def get_user_db() -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    async with async_session_maker() as session:
        yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

    async def on_after_register(self, user: User, request=None):
        print(f"[auth] Registered: {user.email} (role={user.role})")

    async def on_after_login(self, user: User, request=None, response=None):
        print(f"[auth] Login: {user.email}")


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase, Depends(get_user_db)],
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


# --- FastAPI Users instance --------------------------------------------------

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)


# --- Permission guards -------------------------------------------------------
# New system: check whether the user's Role contains a specific permission
# code (e.g. "runs.create"). Replaces the old require_viewer / require_tester /
# require_admin hierarchy.


def require_permission(*perms: str):
    """Dependency factory: require ALL of the listed permissions.

    Usage::

        @router.post("/api/runs")
        async def create_run(
            user: Annotated[User, Depends(require_permission("runs.create"))],
        ):
            ...
    """

    async def _guard(user: Annotated[User, Depends(current_active_user)]) -> User:
        user_perms = set(user.permissions)
        missing = set(perms) - user_perms
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {sorted(missing)}",
            )
        return user

    return _guard


# ── Legacy aliases ───────────────────────────────────────────────────────────
# Keep these so existing router imports don't break during the migration
# period. They'll be removed once all endpoints switch to require_permission.

require_viewer = require_permission("runs.view")
require_tester = require_permission("runs.view", "runs.create")
require_admin = require_permission("users.manage")
