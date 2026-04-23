"""Help portal API — articles, search, popular, feedback.

  GET  /api/help/articles           list with filters (section, q)
  GET  /api/help/articles/popular   top-N articles by 28-day views
  GET  /api/help/articles/{slug}    one article + records a view
  GET  /api/help/sections           enum of sections (stable keys + ru labels)

  POST /api/help/feedback           submit a feedback ticket (any logged user)
  GET  /api/help/admin/feedback     admin inbox
  PATCH /api/help/admin/feedback/{id}   update status / notes / external_id
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.help import (
    FeedbackKind,
    FeedbackStatus,
    FeedbackTicket,
    HelpArticle,
    HelpArticleSection,
    HelpArticleView,
)
from app.models.user import User

router = APIRouter(prefix="/api/help", tags=["help"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class HelpArticleList(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    title: str
    section: str
    excerpt: str | None
    sort_order: int
    views_28d: int
    updated_at: datetime | None
    created_at: datetime


class HelpArticleFull(HelpArticleList):
    body_md: str


class HelpSectionInfo(BaseModel):
    key: str
    label: str
    icon: str


SECTION_LABELS: dict[str, tuple[str, str]] = {
    # key → (Russian label, emoji icon)
    HelpArticleSection.GETTING_STARTED.value: ("Первые шаги", "🚀"),
    HelpArticleSection.RUNS.value: ("Запуски исследования", "▶️"),
    HelpArticleSection.SCENARIOS.value: ("Сценарии", "📝"),
    HelpArticleSection.APPS.value: ("Приложения магазина", "🧩"),
    HelpArticleSection.ADMIN.value: ("Администрирование", "⚙️"),
    HelpArticleSection.API.value: ("API и интеграции", "🔌"),
    HelpArticleSection.TROUBLESHOOTING.value: ("Решение проблем", "🛟"),
}


class FeedbackSubmit(BaseModel):
    kind: FeedbackKind
    subject: str = Field(..., min_length=3, max_length=300)
    body: str = Field(..., min_length=5, max_length=10_000)
    context: dict | None = None


class FeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    user_email: str | None
    kind: str
    subject: str
    body: str
    context: dict | None
    status: str
    external_id: str | None
    admin_notes: str | None
    created_at: datetime
    updated_at: datetime | None


class FeedbackUpdate(BaseModel):
    status: FeedbackStatus | None = None
    admin_notes: str | None = None
    external_id: str | None = None


# ── Sections (enum endpoint) ─────────────────────────────────────────────────


@router.get("/sections", response_model=list[HelpSectionInfo])
async def list_sections() -> list[dict]:
    return [
        {"key": key, "label": label, "icon": icon}
        for key, (label, icon) in SECTION_LABELS.items()
    ]


# ── Articles ─────────────────────────────────────────────────────────────────


@router.get("/articles", response_model=list[HelpArticleList])
async def list_articles(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    section: str | None = Query(None, description="Filter by section key"),
    q: str | None = Query(None, description="Search by title or body substring"),
) -> list[HelpArticle]:
    stmt = select(HelpArticle)
    if section:
        stmt = stmt.where(HelpArticle.section == section)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                HelpArticle.title.ilike(needle),
                HelpArticle.body_md.ilike(needle),
                HelpArticle.excerpt.ilike(needle),
            )
        )
    stmt = stmt.order_by(HelpArticle.section, HelpArticle.sort_order, HelpArticle.title)
    r = await session.execute(stmt)
    return list(r.scalars().all())


@router.get("/articles/popular", response_model=list[HelpArticleList])
async def popular_articles(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    limit: int = 6,
) -> list[HelpArticle]:
    """Top-N by ``views_28d``. We fall back to ``sort_order`` when two
    articles have the same view count so the list is stable."""
    stmt = (
        select(HelpArticle)
        .order_by(HelpArticle.views_28d.desc(), HelpArticle.sort_order)
        .limit(max(1, min(limit, 20)))
    )
    r = await session.execute(stmt)
    return list(r.scalars().all())


@router.get("/articles/{slug}", response_model=HelpArticleFull)
async def get_article(
    slug: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> HelpArticle:
    r = await session.execute(
        select(HelpArticle).where(HelpArticle.slug == slug)
    )
    article = r.scalar_one_or_none()
    if article is None:
        raise HTTPException(404, "Статья не найдена")

    # Record the view — every fetch counts as one. The 28-day cache is
    # refreshed opportunistically here: we recount views from the last
    # 28 days and write the number back. Cheap: ~1 query per article
    # fetch and the result fits in an integer.
    session.add(HelpArticleView(article_id=article.id, user_id=user.id))
    await session.flush()
    cutoff = datetime.now(timezone.utc) - timedelta(days=28)
    count_q = await session.execute(
        select(func.count(HelpArticleView.id)).where(
            HelpArticleView.article_id == article.id,
            HelpArticleView.viewed_at >= cutoff,
        )
    )
    article.views_28d = int(count_q.scalar() or 0)
    await session.commit()
    await session.refresh(article)
    return article


# ── Feedback ─────────────────────────────────────────────────────────────────


@router.post("/feedback", response_model=FeedbackRead, status_code=201)
async def submit_feedback(
    payload: FeedbackSubmit,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> FeedbackTicket:
    ticket = FeedbackTicket(
        user_id=user.id,
        user_email=user.email,
        kind=payload.kind.value,
        subject=payload.subject,
        body=payload.body,
        context=payload.context,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


@router.get("/admin/feedback", response_model=list[FeedbackRead])
async def list_feedback(
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    status: FeedbackStatus | None = None,
    limit: int = 100,
) -> list[FeedbackTicket]:
    stmt = select(FeedbackTicket)
    if status is not None:
        stmt = stmt.where(FeedbackTicket.status == status.value)
    stmt = stmt.order_by(FeedbackTicket.created_at.desc()).limit(
        max(1, min(limit, 500))
    )
    r = await session.execute(stmt)
    return list(r.scalars().all())


@router.patch("/admin/feedback/{ticket_id}", response_model=FeedbackRead)
async def update_feedback(
    ticket_id: UUID,
    payload: FeedbackUpdate,
    _admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> FeedbackTicket:
    row = await session.get(FeedbackTicket, ticket_id)
    if row is None:
        raise HTTPException(404, "Обращение не найдено")
    if payload.status is not None:
        row.status = payload.status.value
    if payload.admin_notes is not None:
        row.admin_notes = payload.admin_notes
    if payload.external_id is not None:
        row.external_id = payload.external_id
    await session.commit()
    await session.refresh(row)
    return row
