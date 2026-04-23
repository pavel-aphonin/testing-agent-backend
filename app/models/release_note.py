"""Release notes + per-user "seen/dismissed" state.

Shown in the sidebar as a "Что нового?" plaque. When the user clicks it
a modal lists notes newest-first. ``ReleaseNoteDismissal`` keeps the
"I've read this" mark per user, persisted server-side — so the badge
follows the user across devices, browsers and session wipes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReleaseNote(Base):
    __tablename__ = "release_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Semver string, e.g. "0.4.2". Unique — one note per version.
    version: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    # Short headline shown in the plaque's hover + list view.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # One-line summary for the collapsed list.
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full body in markdown — rendered in the modal and on the permalink
    # page.
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    # Canonical release date (display). Separate from created_at so
    # backdated or future-dated notes are possible.
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Drafts are created but not visible to regular users until flipped.
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class ReleaseNoteDismissal(Base):
    """Per-user record: this user closed this release note.

    Only the ``dismissed`` row exists — absence of a row means "unread".
    That keeps the bookkeeping simple and the table small (we only
    store an entry when a user actually clicks the X).
    """

    __tablename__ = "release_note_dismissals"
    __table_args__ = (
        UniqueConstraint("user_id", "note_id", name="uq_rn_dismissal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("release_notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
