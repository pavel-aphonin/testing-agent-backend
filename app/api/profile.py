"""/api/profile — self-service profile and password change.

fastapi-users already exposes /users/me, but its update flow doesn't have
a clean "change my password" path that requires the current password as a
defense against drive-by attacks via stolen JWTs. We provide that here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import UserManager, current_active_user
from app.db import get_async_session
from app.models.user import User
from app.schemas.profile import ChangePasswordRequest, ProfileRead

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileRead)
async def read_my_profile(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    payload: ChangePasswordRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    user_db = SQLAlchemyUserDatabase(session, User)
    user_manager = UserManager(user_db)

    # password_helper.verify_and_update returns (bool, new_hash_or_None).
    # We only care about the bool here.
    is_valid, _ = user_manager.password_helper.verify_and_update(
        payload.current_password, user.hashed_password
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    user.hashed_password = user_manager.password_helper.hash(payload.new_password)
    user.must_change_password = False
    session.add(user)
    await session.commit()
