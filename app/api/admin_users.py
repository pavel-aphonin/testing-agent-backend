"""/api/admin/users — admin-only user management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import UserManager, require_admin
from app.db import get_async_session
from app.models.user import User, UserRole
from app.schemas.admin_user import AdminUserCreate, AdminUserRead, AdminUserUpdate
from app.schemas.user import UserCreate

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


_VALID_ROLES = {role.value for role in UserRole}


def _validate_role(role: str | None) -> None:
    if role is not None and role not in _VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role. Must be one of: {sorted(_VALID_ROLES)}",
        )


@router.get("", response_model=list[AdminUserRead])
async def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[User]:
    result = await session.execute(select(User).order_by(User.email))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=AdminUserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: AdminUserCreate,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    _validate_role(payload.role)

    user_db = SQLAlchemyUserDatabase(session, User)
    user_manager = UserManager(user_db)

    try:
        new_user = await user_manager.create(
            UserCreate(
                email=payload.email,
                password=payload.password,
                is_active=True,
                is_verified=True,
                is_superuser=(payload.role == UserRole.ADMIN.value),
                role=payload.role,
                must_change_password=payload.must_change_password,
            )
        )
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        ) from exc

    return new_user


@router.patch("/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    _validate_role(payload.role)

    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.role is not None:
        target.role = payload.role
        target.is_superuser = payload.role == UserRole.ADMIN.value

    if payload.is_active is not None:
        if target.id == admin.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        target.is_active = payload.is_active

    if payload.must_change_password is not None:
        target.must_change_password = payload.must_change_password

    if payload.password is not None:
        # Use the user manager so the password is hashed correctly.
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)
        target.hashed_password = user_manager.password_helper.hash(payload.password)
        target.must_change_password = True

    await session.commit()
    await session.refresh(target)
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    await session.delete(target)
    await session.commit()
