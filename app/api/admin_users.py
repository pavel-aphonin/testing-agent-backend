"""/api/admin/users — user management (requires users.* permissions)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import UserManager, require_permission
from app.db import get_async_session
from app.models.role import Role
from app.models.user import User
from app.schemas.admin_user import AdminUserCreate, AdminUserRead, AdminUserUpdate
from app.schemas.user import UserCreate

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("", response_model=list[AdminUserRead])
async def list_users(
    _user: Annotated[User, Depends(require_permission("users.view"))],
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
    _user: Annotated[User, Depends(require_permission("users.create"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    # Validate role_id exists
    role = await session.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(status_code=422, detail="Role not found")

    user_db = SQLAlchemyUserDatabase(session, User)
    user_manager = UserManager(user_db)

    try:
        new_user = await user_manager.create(
            UserCreate(
                email=payload.email,
                password=payload.password,
                is_active=True,
                is_verified=True,
                is_superuser=(role.code == "admin"),
                role=role.code,
                must_change_password=payload.must_change_password,
            )
        )
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        ) from exc

    # Set role_id (fastapi-users doesn't know about it)
    result = await session.execute(select(User).where(User.id == new_user.id))
    user_obj = result.scalar_one()
    user_obj.role_id = payload.role_id
    user_obj.role = role.code
    await session.commit()
    await session.refresh(user_obj)
    return user_obj


@router.patch("/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    admin: Annotated[User, Depends(require_permission("users.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.role_id is not None:
        role = await session.get(Role, payload.role_id)
        if role is None:
            raise HTTPException(status_code=422, detail="Role not found")
        target.role_id = payload.role_id
        target.role = role.code
        target.is_superuser = role.code == "admin"

    if payload.is_active is not None:
        if target.id == admin.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        target.is_active = payload.is_active

    if payload.must_change_password is not None:
        target.must_change_password = payload.must_change_password

    if payload.password is not None:
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
    admin: Annotated[User, Depends(require_permission("users.delete"))],
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
