"""Release notes API.

  GET    /api/release-notes            public list (newest first, filterable)
  GET    /api/release-notes/unread     current user's unread count (+ first-page)
  GET    /api/release-notes/{version}  one note by semver
  POST   /api/release-notes/{id}/dismiss   current user marks one as read
  POST   /api/release-notes/dismiss-all    mark everything currently published as read

  Admin (behind ``users.view``):
  POST   /api/admin/release-notes        create
  PATCH  /api/admin/release-notes/{id}   edit
  DELETE /api/admin/release-notes/{id}   delete
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.release_note import ReleaseNote, ReleaseNoteDismissal
from app.models.user import User

router = APIRouter(prefix="/api/release-notes", tags=["release-notes"])
admin_router = APIRouter(prefix="/api/admin/release-notes", tags=["release-notes"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class ReleaseNoteSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    version: str
    title: str
    excerpt: str | None
    released_at: datetime
    is_published: bool


class ReleaseNoteFull(ReleaseNoteSummary):
    body_md: str
    created_at: datetime
    updated_at: datetime | None


class ReleaseNoteWithStatus(ReleaseNoteSummary):
    """Summary + whether the *current user* has dismissed it."""

    dismissed: bool = False


class ReleaseNoteFullWithStatus(ReleaseNoteFull):
    dismissed: bool = False


class ReleaseNoteCreate(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(..., min_length=3, max_length=200)
    excerpt: str | None = None
    body_md: str = Field(..., min_length=10)
    released_at: datetime
    is_published: bool = True


class ReleaseNoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    excerpt: str | None = None
    body_md: str | None = None
    released_at: datetime | None = None
    is_published: bool | None = None


class UnreadResponse(BaseModel):
    unread_count: int
    latest: ReleaseNoteWithStatus | None


# ── Public endpoints ─────────────────────────────────────────────────────────


async def _dismissed_ids(session: AsyncSession, user_id: UUID) -> set[UUID]:
    r = await session.execute(
        select(ReleaseNoteDismissal.note_id).where(
            ReleaseNoteDismissal.user_id == user_id
        )
    )
    return {row[0] for row in r.all()}


@router.get("", response_model=list[ReleaseNoteWithStatus])
async def list_release_notes(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    date_from: datetime | None = Query(None, description="Inclusive lower bound on released_at"),
    date_to: datetime | None = Query(None, description="Inclusive upper bound on released_at"),
    include_drafts: bool = Query(
        False, description="Include unpublished drafts (admins only)."
    ),
) -> list[ReleaseNoteWithStatus]:
    stmt = select(ReleaseNote).order_by(ReleaseNote.released_at.desc())
    if date_from:
        stmt = stmt.where(ReleaseNote.released_at >= date_from)
    if date_to:
        stmt = stmt.where(ReleaseNote.released_at <= date_to)

    is_admin = "users.view" in (user.permissions or [])
    if not (include_drafts and is_admin):
        stmt = stmt.where(ReleaseNote.is_published.is_(True))

    res = await session.execute(stmt)
    notes = list(res.scalars().all())
    dismissed = await _dismissed_ids(session, user.id)
    return [
        ReleaseNoteWithStatus(
            id=n.id,
            version=n.version,
            title=n.title,
            excerpt=n.excerpt,
            released_at=n.released_at,
            is_published=n.is_published,
            dismissed=n.id in dismissed,
        )
        for n in notes
    ]


@router.get("/unread", response_model=UnreadResponse)
async def unread_summary(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> UnreadResponse:
    """Cheap endpoint the sidebar polls to decide whether to show the
    red dot on the "Что нового?" plaque + preload the newest note."""
    dismissed = await _dismissed_ids(session, user.id)

    total_q = await session.execute(
        select(func.count(ReleaseNote.id)).where(ReleaseNote.is_published.is_(True))
    )
    total = int(total_q.scalar() or 0)
    unread = total - len(dismissed & await _published_ids(session))

    latest_q = await session.execute(
        select(ReleaseNote)
        .where(ReleaseNote.is_published.is_(True))
        .order_by(ReleaseNote.released_at.desc())
        .limit(1)
    )
    latest = latest_q.scalar_one_or_none()
    return UnreadResponse(
        unread_count=max(0, unread),
        latest=(
            ReleaseNoteWithStatus(
                id=latest.id,
                version=latest.version,
                title=latest.title,
                excerpt=latest.excerpt,
                released_at=latest.released_at,
                is_published=latest.is_published,
                dismissed=latest.id in dismissed,
            )
            if latest
            else None
        ),
    )


async def _published_ids(session: AsyncSession) -> set[UUID]:
    r = await session.execute(
        select(ReleaseNote.id).where(ReleaseNote.is_published.is_(True))
    )
    return {row[0] for row in r.all()}


@router.get("/{version}", response_model=ReleaseNoteFullWithStatus)
async def get_release_note(
    version: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ReleaseNoteFullWithStatus:
    r = await session.execute(
        select(ReleaseNote).where(ReleaseNote.version == version)
    )
    note = r.scalar_one_or_none()
    if note is None:
        raise HTTPException(404, "Заметка не найдена")
    is_admin = "users.view" in (user.permissions or [])
    if not note.is_published and not is_admin:
        raise HTTPException(404, "Заметка не найдена")

    dismissed = await _dismissed_ids(session, user.id)
    return ReleaseNoteFullWithStatus(
        id=note.id,
        version=note.version,
        title=note.title,
        excerpt=note.excerpt,
        body_md=note.body_md,
        released_at=note.released_at,
        is_published=note.is_published,
        created_at=note.created_at,
        updated_at=note.updated_at,
        dismissed=note.id in dismissed,
    )


@router.post("/{note_id}/dismiss", status_code=204)
async def dismiss_one(
    note_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    """Mark a single note as read for the current user. Idempotent —
    upsert-style: if already dismissed we just leave it alone."""
    note = await session.get(ReleaseNote, note_id)
    if note is None:
        raise HTTPException(404, "Заметка не найдена")
    r = await session.execute(
        select(ReleaseNoteDismissal).where(
            ReleaseNoteDismissal.user_id == user.id,
            ReleaseNoteDismissal.note_id == note_id,
        )
    )
    if r.scalar_one_or_none() is None:
        session.add(ReleaseNoteDismissal(user_id=user.id, note_id=note_id))
        await session.commit()


@router.post("/dismiss-all", status_code=204)
async def dismiss_all(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    """Mark every published note as read. Used by "Прочитать всё"."""
    pub_ids = await _published_ids(session)
    already = await _dismissed_ids(session, user.id)
    to_add = pub_ids - already
    for nid in to_add:
        session.add(ReleaseNoteDismissal(user_id=user.id, note_id=nid))
    if to_add:
        await session.commit()


# ── Admin ────────────────────────────────────────────────────────────────────


@admin_router.post("", response_model=ReleaseNoteFull, status_code=201)
async def create_release_note(
    payload: ReleaseNoteCreate,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ReleaseNote:
    # Reject duplicate version early with a nice message.
    dup = await session.execute(
        select(ReleaseNote).where(ReleaseNote.version == payload.version)
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, f"Заметка о версии {payload.version} уже существует")
    note = ReleaseNote(**payload.model_dump())
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


@admin_router.patch("/{note_id}", response_model=ReleaseNoteFull)
async def update_release_note(
    note_id: UUID,
    payload: ReleaseNoteUpdate,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ReleaseNote:
    note = await session.get(ReleaseNote, note_id)
    if note is None:
        raise HTTPException(404, "Заметка не найдена")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(note, k, v)
    await session.commit()
    await session.refresh(note)
    return note


@admin_router.delete("/{note_id}", status_code=204)
async def delete_release_note(
    note_id: UUID,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    note = await session.get(ReleaseNote, note_id)
    if note is None:
        raise HTTPException(404, "Заметка не найдена")
    await session.delete(note)
    await session.commit()
