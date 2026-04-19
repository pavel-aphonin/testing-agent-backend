"""/api/workspaces — workspace CRUD + member management.

Access rules:
  - Any authenticated user can list their own workspaces.
  - Creating a workspace makes the creator the owner.
  - Owners and moderators can add/remove members.
  - Only system admins can archive/restore/delete.
  - Admin dict endpoint lists ALL workspaces (including archived).
"""

from __future__ import annotations

import os
import uuid as _uuid
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WsRole
from app.schemas.workspace import (
    WorkspaceBrief,
    WorkspaceCreate,
    WorkspaceMemberAdd,
    WorkspaceMemberRead,
    WorkspaceRead,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
admin_router = APIRouter(prefix="/api/admin/workspaces", tags=["admin-workspaces"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_perm(user: User, perm: str) -> bool:
    return perm in (user.permissions or [])


async def _get_workspace(
    ws_id: UUID, session: AsyncSession, *, include_archived: bool = False
) -> Workspace:
    result = await session.execute(select(Workspace).where(Workspace.id == ws_id))
    ws = result.scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.is_archived and not include_archived:
        raise HTTPException(status_code=410, detail="Workspace is archived")
    return ws


async def _require_ws_role(
    ws_id: UUID, user: User, session: AsyncSession, min_role: WsRole = WsRole.MODERATOR
) -> WorkspaceMember:
    """Check user has at least `min_role` in the workspace."""
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    rank = {WsRole.MEMBER.value: 0, WsRole.MODERATOR.value: 1, WsRole.OWNER.value: 2}
    if rank.get(member.role, -1) < rank.get(min_role.value, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role.value} role in workspace")
    return member


# ── Public endpoints ─────────────────────────────────────────────────────────

@router.get("/my", response_model=list[WorkspaceBrief])
async def my_workspaces(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[Workspace]:
    """Workspaces the current user belongs to (for the switcher)."""
    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.is_archived == False)  # noqa: E712
        .order_by(Workspace.name)
    )
    return list(result.scalars().all())


@router.get("/{ws_id}", response_model=WorkspaceRead)
async def get_workspace(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    ws = await _get_workspace(ws_id, session)
    # Must be a member (or admin)
    if not _has_perm(user, "users.manage"):
        await _require_ws_role(ws_id, user, session, WsRole.MEMBER)
    return ws


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    # Check code uniqueness
    exists = await session.execute(
        select(Workspace).where(Workspace.code == payload.code)
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Workspace code already exists")

    ws = Workspace(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        created_by_user_id=user.id,
    )
    session.add(ws)
    await session.flush()

    # Creator becomes owner
    member = WorkspaceMember(
        workspace_id=ws.id,
        user_id=user.id,
        role=WsRole.OWNER.value,
    )
    session.add(member)
    await session.commit()
    await session.refresh(ws)
    return ws


# ── Logo upload ──────────────────────────────────────────────────────────────


def _logos_dir() -> Path:
    """Where workspace logos live on disk. Inside the shared app uploads
    volume so the file is visible to both backend container and frontend
    (which serves it via /api/workspaces/{id}/logo)."""
    base = Path(settings.app_uploads_dir) / "workspace-logos"
    base.mkdir(parents=True, exist_ok=True)
    return base


@router.post("/{ws_id}/logo", response_model=WorkspaceRead)
async def upload_logo(
    ws_id: UUID,
    file: UploadFile,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    ws = await _get_workspace(ws_id, session)
    if not _has_perm(user, "users.view"):
        await _require_ws_role(ws_id, user, session, WsRole.MODERATOR)

    if not file.filename:
        raise HTTPException(400, "Имя файла не указано")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        raise HTTPException(400, "Поддерживаются только PNG/JPG/GIF/WebP/SVG")

    content = await file.read()
    if len(content) > 2_000_000:  # 2 MB
        raise HTTPException(413, "Файл слишком большой (макс. 2 МБ)")

    fname = f"{ws_id}_{_uuid.uuid4().hex[:8]}{ext}"
    fpath = _logos_dir() / fname
    fpath.write_bytes(content)

    # Delete old logo file if present
    if ws.logo_path:
        old = Path(settings.app_uploads_dir) / ws.logo_path
        if old.exists() and old.is_file():
            try:
                old.unlink()
            except OSError:
                pass

    ws.logo_path = f"workspace-logos/{fname}"
    await session.commit()
    await session.refresh(ws)
    return ws


@router.get("/{ws_id}/logo")
async def get_logo(
    ws_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Public endpoint to serve workspace logo (no auth — used in <img>)."""
    ws = await session.get(Workspace, ws_id)
    if ws is None or not ws.logo_path:
        raise HTTPException(404, "No logo")
    fpath = Path(settings.app_uploads_dir) / ws.logo_path
    if not fpath.exists():
        raise HTTPException(404, "Logo file missing")
    return FileResponse(fpath)


@router.patch("/{ws_id}", response_model=WorkspaceRead)
async def update_workspace(
    ws_id: UUID,
    payload: WorkspaceUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    ws = await _get_workspace(ws_id, session)
    if not _has_perm(user, "users.manage"):
        await _require_ws_role(ws_id, user, session, WsRole.MODERATOR)

    if payload.name is not None:
        ws.name = payload.name
    if payload.description is not None:
        ws.description = payload.description

    await session.commit()
    await session.refresh(ws)
    return ws


# ── Members ──────────────────────────────────────────────────────────────────

@router.get("/{ws_id}/members", response_model=list[WorkspaceMemberRead])
async def list_members(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    ws = await _get_workspace(ws_id, session)
    if not _has_perm(user, "users.manage"):
        await _require_ws_role(ws_id, user, session, WsRole.MEMBER)

    result = await session.execute(
        select(WorkspaceMember)
        .options(selectinload(WorkspaceMember.user))
        .where(WorkspaceMember.workspace_id == ws_id)
        .order_by(WorkspaceMember.joined_at)
    )
    members = result.scalars().all()
    return [
        {
            "id": m.id,
            "workspace_id": m.workspace_id,
            "user_id": m.user_id,
            "user_email": m.user.email if m.user else "",
            "role": m.role,
            "joined_at": m.joined_at,
        }
        for m in members
    ]


@router.post("/{ws_id}/members", response_model=WorkspaceMemberRead, status_code=201)
async def add_member(
    ws_id: UUID,
    payload: WorkspaceMemberAdd,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    ws = await _get_workspace(ws_id, session)
    if not _has_perm(user, "users.manage"):
        await _require_ws_role(ws_id, user, session, WsRole.MODERATOR)

    # Validate target user exists
    target = await session.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check not already a member
    exists = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == payload.user_id,
        )
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member")

    valid_roles = {WsRole.MEMBER.value, WsRole.MODERATOR.value}
    role = payload.role if payload.role in valid_roles else WsRole.MEMBER.value

    member = WorkspaceMember(
        workspace_id=ws_id,
        user_id=payload.user_id,
        role=role,
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return {
        "id": member.id,
        "workspace_id": member.workspace_id,
        "user_id": member.user_id,
        "user_email": target.email,
        "role": member.role,
        "joined_at": member.joined_at,
    }


@router.delete("/{ws_id}/members/{user_id}", status_code=204)
async def remove_member(
    ws_id: UUID,
    user_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    ws = await _get_workspace(ws_id, session)
    if not _has_perm(user, "users.manage"):
        await _require_ws_role(ws_id, user, session, WsRole.MODERATOR)

    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    # Can't remove the last owner
    if member.role == WsRole.OWNER.value:
        owners = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.role == WsRole.OWNER.value,
            )
        )
        if len(owners.scalars().all()) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last owner. Transfer ownership first.",
            )

    await session.delete(member)
    await session.commit()


# ── Admin endpoints ──────────────────────────────────────────────────────────

@admin_router.get("", response_model=list[WorkspaceRead])
async def admin_list_workspaces(
    _user: Annotated[User, Depends(require_permission("dictionaries.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[Workspace]:
    """All workspaces including archived — for the admin dictionaries page."""
    result = await session.execute(select(Workspace).order_by(Workspace.name))
    return list(result.scalars().all())


@admin_router.post("/{ws_id}/archive", response_model=WorkspaceRead)
async def archive_workspace(
    ws_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    ws = await _get_workspace(ws_id, session, include_archived=True)
    ws.is_archived = True
    await session.commit()
    await session.refresh(ws)
    return ws


@admin_router.post("/{ws_id}/restore", response_model=WorkspaceRead)
async def restore_workspace(
    ws_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Workspace:
    ws = await _get_workspace(ws_id, session, include_archived=True)
    ws.is_archived = False
    await session.commit()
    await session.refresh(ws)
    return ws


@admin_router.delete("/{ws_id}", status_code=204)
async def delete_workspace(
    ws_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.delete"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    ws = await _get_workspace(ws_id, session, include_archived=True)
    await session.delete(ws)
    await session.commit()
