"""/api/profile — self-service profile and password change.

fastapi-users already exposes /users/me, but its update flow doesn't have
a clean "change my password" path that requires the current password as a
defense against drive-by attacks via stolen JWTs. We provide that here.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import UserManager, current_active_user
from app.config import settings
from app.db import get_async_session
from app.models.user import User
from app.schemas.profile import ChangePasswordRequest, ProfileRead

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Avatars live here and are served by the static mount declared in
# main.py at ``/avatar-assets/``.
AVATARS_SUBDIR = "avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB — same ceiling as logos
AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _avatars_dir() -> Path:
    p = Path(settings.app_uploads_dir) / AVATARS_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _unlink(relpath: str | None) -> None:
    if not relpath:
        return
    try:
        (Path(settings.app_uploads_dir) / relpath).unlink()
    except OSError:
        pass


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


# ── Avatar ───────────────────────────────────────────────────────────────────


@router.post("/avatar", response_model=ProfileRead)
async def upload_avatar(
    file: UploadFile,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    """Upload a new avatar. Previous file (if any) is deleted after
    the DB row is updated. Random tag in filename dodges browser cache
    on the old URL."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AVATAR_EXTENSIONS:
        raise HTTPException(400, "Поддерживаются PNG, JPG, WebP, GIF")
    blob = file.file.read()
    if len(blob) > MAX_AVATAR_BYTES:
        raise HTTPException(
            400, f"Файл больше {MAX_AVATAR_BYTES // 1024 // 1024} МБ"
        )

    tag = secrets.token_hex(4)
    fname = f"avatar-{user.id}-{tag}{ext}"
    (_avatars_dir() / fname).write_bytes(blob)

    old = user.avatar_path
    user.avatar_path = f"{AVATARS_SUBDIR}/{fname}"
    await session.commit()
    await session.refresh(user)
    _unlink(old)
    return user


@router.delete("/avatar", response_model=ProfileRead)
async def delete_avatar(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    old = user.avatar_path
    user.avatar_path = None
    await session.commit()
    await session.refresh(user)
    _unlink(old)
    return user
