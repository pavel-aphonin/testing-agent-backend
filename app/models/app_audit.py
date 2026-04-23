"""Audit log for app installations in a workspace.

One row per meaningful change to an installation:

  INSTALLED       — workspace got a fresh install of an app
  VERSION_CHANGED — someone upgraded or rolled back the version_id
  SETTINGS_CHANGED — the manifest-driven settings JSONB was edited
  ENABLED / DISABLED — someone flipped the is_enabled toggle
  UNINSTALLED     — the installation row was deleted

Shown under the "История" tab of the workspace apps page so a team can
see who did what. The table is append-only; we never edit or delete
rows (even if the installation itself goes away, the audit row stays
to preserve the history).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppInstallationAuditAction(StrEnum):
    INSTALLED = "installed"
    VERSION_CHANGED = "version_changed"
    SETTINGS_CHANGED = "settings_changed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"


class AppInstallationAudit(Base):
    __tablename__ = "app_installation_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Denormalized so the row survives when the install/package is gone.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    app_package_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        # Note: no FK — we want rows to survive uninstall.
        nullable=True,
        index=True,
    )

    # Copy of names at the moment of the event so the history remains
    # readable even if the package is later renamed or removed.
    package_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    action: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # For VERSION_CHANGED: from/to semver strings.
    from_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_version: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Free-form extras — e.g. list of setting keys that changed, or the
    # reason for an uninstall. Client can render as "details" on hover.
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Copy of the email too — again, so history stays readable if the
    # user is deleted.
    user_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
