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


# ── Permission shortcuts ─────────────────────────────────────────────────────
# PER-106 #10: the original ``require_viewer`` / ``require_tester``
# aliases were removed once every router migrated to the resource-
# specific permission codes (scenarios.create, test_data.edit, etc.).
# ``require_admin`` remains because every admin-only endpoint is by
# definition gated on the same ``users.view`` permission, and a single
# named shortcut makes the intent obvious at the call site.

require_admin = require_permission("users.view")


# ── Workspace access guard ───────────────────────────────────────────────────
# PER-106 #2/#3/#4: every workspace-scoped resource (runs / scenarios /
# test_data / knowledge / dashboards) must verify that the requesting
# user is a member of the workspace before listing, reading, creating,
# updating, or deleting. Several routers were filtering by ``workspace_id``
# without that check — a tester could pass a different workspace_id and
# read or write into it.
#
# Single helper, used everywhere, so the policy stays in one place and
# is easy to audit. Returns True/False rather than raising so callers
# can decide between 403 and 404 depending on whether they want to hide
# the workspace's existence.


async def is_workspace_member(
    session,
    user_id: "UUID",
    workspace_id: "UUID",
) -> bool:
    """True if ``user_id`` belongs to ``workspace_id`` as a member.

    Admins (superusers) are members of every workspace by definition —
    short-circuit to True. Non-admins must have an explicit
    ``workspace_members`` row.
    """
    from sqlalchemy import select
    from app.models.user import User as UserModel
    from app.models.workspace import WorkspaceMember

    user = await session.get(UserModel, user_id)
    if user is None:
        return False
    if user.is_superuser:
        return True
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.user_id == user_id,
        WorkspaceMember.workspace_id == workspace_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def require_workspace_membership(
    session,
    user: "User",
    workspace_id: "UUID | None",
) -> None:
    """Raise 403 if the caller does not belong to ``workspace_id``.

    ``workspace_id=None`` is treated as "no workspace requested" and
    short-circuits to allow — callers wanting to forbid the workspace-
    less case should check explicitly.
    """
    if workspace_id is None:
        return
    ok = await is_workspace_member(session, user.id, workspace_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )
