"""fastapi-users wiring: JWT backend, user manager, role-based dependencies."""

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
from app.models.user import User, UserRole

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


# --- Role guards -------------------------------------------------------------

_ROLE_ORDER = {
    UserRole.VIEWER.value: 0,
    UserRole.TESTER.value: 1,
    UserRole.ADMIN.value: 2,
}


def require_role(minimum: UserRole):
    """Dependency factory: require at least `minimum` role."""

    async def _guard(user: Annotated[User, Depends(current_active_user)]) -> User:
        user_level = _ROLE_ORDER.get(user.role, -1)
        required_level = _ROLE_ORDER[minimum.value]
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {minimum.value} role or higher",
            )
        return user

    return _guard


require_viewer = require_role(UserRole.VIEWER)
require_tester = require_role(UserRole.TESTER)
require_admin = require_role(UserRole.ADMIN)
