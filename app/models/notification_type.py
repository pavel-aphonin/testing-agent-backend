"""NotificationType — global dictionary of notification kinds.

A notification type defines display attributes (icon, color, title
template) for one kind of notification. Some types are system
(e.g. workspace_invite) and can't be deleted; admins can create custom
types alongside.

Per-workspace subscription is tracked in WorkspaceNotificationSetting:
if a row exists with is_enabled=False, members of that workspace won't
receive notifications of that type. Default (no row) = subscribed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class NotificationType(Base):
    __tablename__ = "notification_types"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hex color for the type tag, e.g. "#EE3424"
    color: Mapped[str] = mapped_column(String(20), default="#888888", nullable=False)
    # Ant Design icon name without the suffix, e.g. "Bell", "Mail", "Bug"
    icon: Mapped[str] = mapped_column(String(50), default="Bell", nullable=False)
    # Title template with {placeholders} pulled from notification.payload.
    # Optional — when null, the title field on the notification is used as-is.
    template: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tree groups (admins may want to organise long lists)
    parent_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class WorkspaceNotificationSetting(Base):
    """Per-workspace toggle for one notification type."""

    __tablename__ = "workspace_notification_settings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "notification_type_id", name="uq_ws_notif_setting"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
