"""Role model — flexible RBAC with JSONB permissions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Human-readable name shown in the UI dropdown ("Тестировщик QA").
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    # Machine-readable slug used in code references and migrations ("tester").
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # System roles (viewer/tester/admin) can't be deleted or have their
    # code changed. Their permissions CAN be extended by admins though.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Array of permission codes, e.g. ["runs.view", "runs.create", ...].
    # Stored as JSONB so Postgres can index / query into it if needed.
    permissions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Tree structure: optional parent (folder). Unlimited nesting depth.
    parent_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # When true, this row is just a grouping folder — permissions and code
    # are still stored but ignored by the auth system.
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
