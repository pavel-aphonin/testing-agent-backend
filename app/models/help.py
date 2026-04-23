"""Help portal — articles + views + feedback tickets.

Articles are markdown-bodied KB entries rendered on the «Справка» page.
Views are logged per-visit so "popular" can reflect actual recent usage
(last 28 days, exponentially close to "last month") without a tracking
pixel or external analytics. Feedback tickets are what the user submits
through the form on the help page — they land in an admin inbox.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class HelpArticleSection(StrEnum):
    """Top-level sections on the help portal.

    Kept as a Python enum so the frontend can get them from an enum
    endpoint rather than hard-coding the list. Adding a new section is
    a one-line change here (plus writing an article for it).
    """

    GETTING_STARTED = "getting_started"
    RUNS = "runs"
    SCENARIOS = "scenarios"
    APPS = "apps"
    ADMIN = "admin"
    API = "api"
    TROUBLESHOOTING = "troubleshooting"


class HelpArticle(Base):
    __tablename__ = "help_articles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    section: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Cached 28-day view count, recomputed periodically. Avoids a join
    # to help_article_views on every list request.
    views_28d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class HelpArticleView(Base):
    __tablename__ = "help_article_views"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("help_articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class FeedbackKind(StrEnum):
    BUG = "bug"
    QUESTION = "question"
    PROPOSAL = "proposal"
    OTHER = "other"


class FeedbackStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class FeedbackTicket(Base):
    __tablename__ = "feedback_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # URL, article slug, browser info, etc.
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), default=FeedbackStatus.NEW.value, nullable=False, index=True
    )
    # Populated once a ticket is synced to Jira / another tracker.
    external_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
