"""/api/dictionaries/roles — RBAC role management.

Requires ``dictionaries.view`` to list, ``dictionaries.create`` / edit / delete
to mutate. System roles (viewer / tester / admin) cannot be deleted but
their permissions CAN be adjusted.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import require_permission
from app.db import get_async_session
from app.models.role import Role
from app.models.user import User
from app.permissions import ALL_PERMISSIONS, PERMISSION_SECTIONS
from app.schemas.role import RoleCreate, RoleRead, RoleUpdate

router = APIRouter(prefix="/api/dictionaries/roles", tags=["dictionaries-roles"])


@router.get("/permissions", response_model=dict)
async def get_permissions_registry(
    _user: Annotated[User, Depends(require_permission("dictionaries.view"))],
) -> dict:
    """Return the full permissions registry so the frontend can render
    the CRUD checkbox matrix when editing a role."""
    return {"sections": PERMISSION_SECTIONS}


@router.get("", response_model=list[RoleRead])
async def list_roles(
    _user: Annotated[User, Depends(require_permission("dictionaries.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[Role]:
    result = await session.execute(select(Role).order_by(Role.name))
    return list(result.scalars().all())


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    _user: Annotated[User, Depends(require_permission("dictionaries.create"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Role:
    # Validate permission codes
    invalid = set(payload.permissions) - ALL_PERMISSIONS
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown permission codes: {sorted(invalid)}",
        )

    # Uniqueness check
    exists = await session.execute(
        select(Role).where((Role.code == payload.code) | (Role.name == payload.name))
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role with this name or code already exists")

    # Validate parent exists if specified
    if payload.parent_id:
        parent = await session.get(Role, payload.parent_id)
        if parent is None:
            raise HTTPException(404, "Parent role not found")

    role = Role(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        is_system=False,
        permissions=sorted(payload.permissions),
        parent_id=payload.parent_id,
        is_group=payload.is_group,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    _user: Annotated[User, Depends(require_permission("dictionaries.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Role:
    result = await session.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    if payload.name is not None:
        # System roles can't have their code changed, but name is OK
        role.name = payload.name

    if payload.description is not None:
        role.description = payload.description

    if payload.permissions is not None:
        invalid = set(payload.permissions) - ALL_PERMISSIONS
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown permission codes: {sorted(invalid)}",
            )
        role.permissions = sorted(payload.permissions)

    if payload.parent_id is not None:
        # Cycle check: walk up the new parent chain, must not hit `role.id`
        if payload.parent_id == role.id:
            raise HTTPException(400, "Role cannot be its own parent")
        cursor_id = payload.parent_id
        depth = 0
        while cursor_id is not None and depth < 100:
            if cursor_id == role.id:
                raise HTTPException(400, "Cycle detected: cannot make node a descendant of itself")
            cursor = await session.get(Role, cursor_id)
            if cursor is None:
                break
            cursor_id = cursor.parent_id
            depth += 1
        role.parent_id = payload.parent_id

    await session.commit()
    await session.refresh(role)
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.delete"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    if role.is_system:
        raise HTTPException(
            status_code=400,
            detail="System roles cannot be deleted. You can modify their permissions instead.",
        )

    # Check if any users still use this role
    from app.models.user import User as UserModel

    users_q = await session.execute(
        select(UserModel).where(UserModel.role_id == role_id).limit(1)
    )
    if users_q.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Cannot delete role while users are assigned to it. Reassign them first.",
        )

    await session.delete(role)
    await session.commit()
