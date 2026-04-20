"""/api/notifications + /api/invitations.

The bell icon polls /api/notifications. Workspace invitations show up
as one type of notification AND have their own accept/decline endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.notification import Notification, WorkspaceInvitation
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WsRole
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
inv_router = APIRouter(prefix="/api/invitations", tags=["invitations"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str | None
    payload: dict | None
    action_url: str | None
    is_read: bool
    created_at: datetime


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    workspace_name: str
    inviter_email: str
    invitee_user_id: UUID
    role: str
    status: str
    created_at: datetime
    responded_at: datetime | None


class InviteCreate(BaseModel):
    workspace_id: UUID
    invitee_user_id: UUID
    role: str = "member"


# ── Notifications ────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    unread_only: bool = False,
) -> list[Notification]:
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.is_read == False)  # noqa: E712
    q = q.order_by(Notification.created_at.desc()).limit(50)
    result = await session.execute(q)
    return list(result.scalars().all())


@router.get("/unread-count")
async def unread_count(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Cheap counter for the bell badge — polled every few seconds."""
    result = await session.execute(
        select(Notification).where(
            Notification.user_id == user.id,
            Notification.is_read == False,  # noqa: E712
        )
    )
    return {"count": len(list(result.scalars().all()))}


@router.post("/{notif_id}/read", status_code=204)
async def mark_read(
    notif_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(Notification).where(
            Notification.id == notif_id,
            Notification.user_id == user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        raise HTTPException(404, "Notification not found")
    notif.is_read = True
    await session.commit()


@router.post("/read-all", status_code=204)
async def mark_all_read(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read == False)  # noqa: E712
        .values(is_read=True)
    )
    await session.commit()


# ── Invitations ──────────────────────────────────────────────────────────────

async def _check_can_invite(
    workspace_id: UUID, user: User, session: AsyncSession
) -> None:
    """Caller must be moderator/owner in the workspace OR a system admin."""
    if "users.view" in (user.permissions or []):
        return  # system admin bypass
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None or member.role not in (
        WsRole.MODERATOR.value, WsRole.OWNER.value
    ):
        raise HTTPException(403, "Requires moderator or owner role in this workspace")


@inv_router.post("", response_model=InvitationRead, status_code=201)
async def create_invitation(
    payload: InviteCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Send an invitation. Creates a notification for the invitee."""
    ws = await session.get(Workspace, payload.workspace_id)
    if ws is None or ws.is_archived:
        raise HTTPException(404, "Workspace not found")
    invitee = await session.get(User, payload.invitee_user_id)
    if invitee is None:
        raise HTTPException(404, "Invitee not found")
    await _check_can_invite(payload.workspace_id, user, session)

    # Already a member?
    existing = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == payload.workspace_id,
            WorkspaceMember.user_id == payload.invitee_user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "User is already a member")

    # Pending invite already exists?
    existing_inv = await session.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == payload.workspace_id,
            WorkspaceInvitation.invitee_user_id == payload.invitee_user_id,
            WorkspaceInvitation.status == "pending",
        )
    )
    if existing_inv.scalar_one_or_none():
        raise HTTPException(409, "Pending invitation already exists")

    valid_roles = {WsRole.MEMBER.value, WsRole.MODERATOR.value}
    role = payload.role if payload.role in valid_roles else WsRole.MEMBER.value

    inv = WorkspaceInvitation(
        workspace_id=payload.workspace_id,
        inviter_user_id=user.id,
        invitee_user_id=payload.invitee_user_id,
        role=role,
    )
    session.add(inv)
    await session.flush()

    # Create notification for the invitee
    notif = Notification(
        user_id=payload.invitee_user_id,
        type="workspace_invite",
        title=f"Приглашение в «{ws.name}»",
        body=f"{user.email} приглашает вас как {role}",
        payload={
            "invitation_id": str(inv.id),
            "workspace_id": str(ws.id),
            "workspace_name": ws.name,
            "role": role,
        },
        action_url=f"/invitations/{inv.id}",
    )
    session.add(notif)
    await session.commit()
    await session.refresh(inv)

    return {
        "id": inv.id,
        "workspace_id": inv.workspace_id,
        "workspace_name": ws.name,
        "inviter_email": user.email,
        "invitee_user_id": inv.invitee_user_id,
        "role": inv.role,
        "status": inv.status,
        "created_at": inv.created_at,
        "responded_at": inv.responded_at,
    }


@inv_router.get("/my", response_model=list[InvitationRead])
async def my_invitations(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    pending_only: bool = True,
) -> list[dict]:
    q = select(WorkspaceInvitation).where(WorkspaceInvitation.invitee_user_id == user.id)
    if pending_only:
        q = q.where(WorkspaceInvitation.status == "pending")
    q = q.order_by(WorkspaceInvitation.created_at.desc())
    result = await session.execute(q)
    invs = result.scalars().all()

    out = []
    for inv in invs:
        ws = await session.get(Workspace, inv.workspace_id)
        inviter = await session.get(User, inv.inviter_user_id) if inv.inviter_user_id else None
        out.append({
            "id": inv.id,
            "workspace_id": inv.workspace_id,
            "workspace_name": ws.name if ws else "(удалено)",
            "inviter_email": inviter.email if inviter else "(удалён)",
            "invitee_user_id": inv.invitee_user_id,
            "role": inv.role,
            "status": inv.status,
            "created_at": inv.created_at,
            "responded_at": inv.responded_at,
        })
    return out


@inv_router.post("/{inv_id}/accept", status_code=200)
async def accept_invitation(
    inv_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    inv = await session.get(WorkspaceInvitation, inv_id)
    if inv is None:
        raise HTTPException(404, "Invitation not found")
    if inv.invitee_user_id != user.id:
        raise HTTPException(403, "Not your invitation")
    if inv.status != "pending":
        raise HTTPException(400, f"Invitation is already {inv.status}")

    # Enforce max_members
    from app.api.workspaces import _check_member_limit
    await _check_member_limit(inv.workspace_id, session)

    # Add as member
    member = WorkspaceMember(
        workspace_id=inv.workspace_id,
        user_id=user.id,
        role=inv.role,
    )
    session.add(member)
    inv.status = "accepted"
    inv.responded_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": "accepted", "workspace_id": str(inv.workspace_id)}


@inv_router.post("/{inv_id}/decline", status_code=200)
async def decline_invitation(
    inv_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    inv = await session.get(WorkspaceInvitation, inv_id)
    if inv is None:
        raise HTTPException(404, "Invitation not found")
    if inv.invitee_user_id != user.id:
        raise HTTPException(403, "Not your invitation")
    if inv.status != "pending":
        raise HTTPException(400, f"Invitation is already {inv.status}")

    inv.status = "declined"
    inv.responded_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": "declined"}
