"""/api/dictionaries/notification-types — admin CRUD for the global type registry.
   /api/workspaces/{ws_id}/notification-settings — per-workspace toggles.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.notification_type import (
    NotificationType,
    WorkspaceNotificationSetting,
)
from app.models.user import User
from app.models.workspace import WorkspaceMember, WsRole
from app.schemas.notification_type import (
    NotificationTypeCreate,
    NotificationTypeRead,
    NotificationTypeUpdate,
    WorkspaceNotificationSettingRead,
    WorkspaceNotificationSettingUpsert,
)

types_router = APIRouter(prefix="/api/dictionaries/notification-types", tags=["notification-types"])
ws_router = APIRouter(prefix="/api/workspaces", tags=["workspace-notification-settings"])


# ── Type CRUD ────────────────────────────────────────────────────────────────

@types_router.get("", response_model=list[NotificationTypeRead])
async def list_types(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[NotificationType]:
    """Open to all authenticated users — frontend needs this to render
    the bell dropdown with correct icons/colors."""
    result = await session.execute(
        select(NotificationType).order_by(NotificationType.name)
    )
    return list(result.scalars().all())


@types_router.post("", response_model=NotificationTypeRead, status_code=201)
async def create_type(
    payload: NotificationTypeCreate,
    _user: Annotated[User, Depends(require_permission("dictionaries.create"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> NotificationType:
    exists = await session.execute(
        select(NotificationType).where(NotificationType.code == payload.code)
    )
    if exists.scalar_one_or_none():
        raise HTTPException(409, "Type with this code already exists")

    if payload.parent_id:
        parent = await session.get(NotificationType, payload.parent_id)
        if parent is None:
            raise HTTPException(404, "Parent not found")

    t = NotificationType(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        icon=payload.icon,
        template=payload.template,
        parent_id=payload.parent_id,
        is_group=payload.is_group,
        is_system=False,
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


@types_router.patch("/{type_id}", response_model=NotificationTypeRead)
async def update_type(
    type_id: UUID,
    payload: NotificationTypeUpdate,
    _user: Annotated[User, Depends(require_permission("dictionaries.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> NotificationType:
    t = await session.get(NotificationType, type_id)
    if t is None:
        raise HTTPException(404, "Type not found")

    if payload.name is not None:
        t.name = payload.name
    if payload.description is not None:
        t.description = payload.description
    if payload.color is not None:
        t.color = payload.color
    if payload.icon is not None:
        t.icon = payload.icon
    if payload.template is not None:
        t.template = payload.template
    if payload.parent_id is not None:
        if payload.parent_id == t.id:
            raise HTTPException(400, "Cannot be own parent")
        cursor_id = payload.parent_id
        for _ in range(100):
            if cursor_id is None:
                break
            if cursor_id == t.id:
                raise HTTPException(400, "Cycle detected")
            cursor = await session.get(NotificationType, cursor_id)
            if cursor is None:
                break
            cursor_id = cursor.parent_id
        t.parent_id = payload.parent_id

    await session.commit()
    await session.refresh(t)
    return t


@types_router.delete("/{type_id}", status_code=204)
async def delete_type(
    type_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.delete"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    t = await session.get(NotificationType, type_id)
    if t is None:
        raise HTTPException(404, "Type not found")
    if t.is_system:
        raise HTTPException(400, "System notification types cannot be deleted")
    await session.delete(t)
    await session.commit()


# ── Per-workspace settings ───────────────────────────────────────────────────

async def _check_can_edit_ws(
    workspace_id: UUID, user: User, session: AsyncSession
) -> None:
    if "users.view" in (user.permissions or []):
        return
    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = res.scalar_one_or_none()
    if member is None or member.role not in (WsRole.MODERATOR.value, WsRole.OWNER.value):
        raise HTTPException(403, "Requires moderator or owner role in workspace")


async def _check_can_view_ws(
    workspace_id: UUID, user: User, session: AsyncSession
) -> None:
    if "users.view" in (user.permissions or []):
        return
    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if res.scalar_one_or_none() is None:
        raise HTTPException(403, "Not a member of this workspace")


@ws_router.get("/{ws_id}/notification-settings", response_model=list[WorkspaceNotificationSettingRead])
async def list_settings(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[WorkspaceNotificationSetting]:
    await _check_can_view_ws(ws_id, user, session)
    res = await session.execute(
        select(WorkspaceNotificationSetting).where(
            WorkspaceNotificationSetting.workspace_id == ws_id,
        )
    )
    return list(res.scalars().all())


@ws_router.put("/{ws_id}/notification-settings", response_model=WorkspaceNotificationSettingRead)
async def upsert_setting(
    ws_id: UUID,
    payload: WorkspaceNotificationSettingUpsert,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WorkspaceNotificationSetting:
    await _check_can_edit_ws(ws_id, user, session)

    t = await session.get(NotificationType, payload.notification_type_id)
    if t is None:
        raise HTTPException(404, "Notification type not found")

    res = await session.execute(
        select(WorkspaceNotificationSetting).where(
            WorkspaceNotificationSetting.workspace_id == ws_id,
            WorkspaceNotificationSetting.notification_type_id == payload.notification_type_id,
        )
    )
    existing = res.scalar_one_or_none()
    if existing is None:
        existing = WorkspaceNotificationSetting(
            workspace_id=ws_id,
            notification_type_id=payload.notification_type_id,
            is_enabled=payload.is_enabled,
        )
        session.add(existing)
    else:
        existing.is_enabled = payload.is_enabled

    await session.commit()
    await session.refresh(existing)
    return existing
