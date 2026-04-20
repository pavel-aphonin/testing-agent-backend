"""Per-user persisted table preferences (column visibility / sort / filters).

One row per (user, table_key). The frontend identifies each list page
by a stable string key (e.g. "runs", "users", "dict.roles") and reads
its preferences on mount, writes them on change.

Stored as JSONB so frontend can evolve the shape without a migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserTablePref(Base):
    __tablename__ = "user_table_prefs"
    __table_args__ = (
        UniqueConstraint("user_id", "table_key", name="uq_user_table_pref"),
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
    table_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # { visible_columns: ["name", "status"], sort: {col, dir}, filters: {...} }
    prefs: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
