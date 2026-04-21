"""Extension apps — the "AppStore" of Markov.

Schema:
  app_packages             — catalog entries (one per app concept)
  app_package_versions     — uploaded bundles, one per semver release
  app_installations        — per-workspace installs, bound to a version
  app_reviews              — ratings + comments on an app

Bundle layout (ZIP):
  /manifest.json    metadata + UI slots + settings schema + hooks
  /frontend/        static assets (iframe entry)
  /logic/           server-side scripts (Python callables referenced
                    from manifest.hooks)
  /logo.png         app icon
  /screenshots/     images for the detail page
  /README.md        rendered on the store page

Manifest spec is enforced via Pydantic in schemas/app_package.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppApprovalStatus(StrEnum):
    DRAFT = "draft"           # Uploaded, not submitted for review
    PENDING = "pending"       # Submitted, awaiting admin decision
    APPROVED = "approved"     # Published — visible in catalog
    REJECTED = "rejected"     # Declined by admin


class AppPackage(Base):
    __tablename__ = "app_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Slug, e.g. "jira-integration" — stable across versions
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "integration" | "automation" | "visualization" | "utility" | ...
    category: Mapped[str] = mapped_column(String(50), default="utility", nullable=False)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Path (under app_uploads_dir) to the logo of the *latest* approved version.
    logo_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Public = searchable in the store by anyone.
    # Private = visible only to members of owner_workspace_id.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    owner_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    approval_status: Mapped[str] = mapped_column(
        String(20), default=AppApprovalStatus.DRAFT.value, nullable=False, index=True
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class AppPackageVersion(Base):
    __tablename__ = "app_package_versions"
    __table_args__ = (
        UniqueConstraint("app_package_id", "version", name="uq_app_pkg_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    app_package_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Semver string, e.g. "1.2.3"
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    # Parsed + validated manifest.json (canonical form)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Path under app_uploads_dir to the extracted bundle directory
    bundle_path: Mapped[str] = mapped_column(Text, nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Size of the original zip in bytes
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Deprecated versions still work for existing installations but are
    # hidden from the "update available" UI.
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppInstallation(Base):
    __tablename__ = "app_installations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "app_package_id", name="uq_app_install_ws_pkg"
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
    app_package_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_package_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # User-supplied values matching manifest.settings_schema
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    installed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class AppReview(Base):
    __tablename__ = "app_reviews"
    __table_args__ = (
        UniqueConstraint("app_package_id", "user_id", name="uq_app_review"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    app_package_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
