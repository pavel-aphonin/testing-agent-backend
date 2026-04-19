"""Attribute system — flexible per-object key/value settings.

An ``Attribute`` is a definition (like a column in a spreadsheet):
  - code: machine-readable slug ("theme", "max_members")
  - data_type: string / number / boolean / enum
  - scope: workspace (per-workspace global) | user (per-user-per-workspace)
  - applies_to: where this attribute can be attached ("workspace" | "role" | …)

An ``AttributeValue`` is the actual value attached to a specific object.
For workspace-scope: entity = workspace_id.
For user-scope: entity = workspace_member_id (so the same user has
different theme in different workspaces).

Supports tree groups too — same parent_id / is_group convention as roles
and workspaces.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Attribute(Base):
    __tablename__ = "attributes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "string" | "number" | "boolean" | "enum"
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # For data_type="enum": list of allowed string values.
    enum_values: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Default value (any JSON-serializable scalar matching data_type).
    default_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # "workspace" — value is shared across all members of the workspace
    # "user"      — each member has their own value within the workspace
    scope: Mapped[str] = mapped_column(String(20), default="workspace", nullable=False)

    # Which kind of entity can this be attached to.
    # Currently we support "workspace" and "user_workspace" (the membership row).
    applies_to: Mapped[str] = mapped_column(String(50), default="workspace", nullable=False)

    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tree structure — same as roles/workspaces
    parent_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="SET NULL"),
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


class AttributeValue(Base):
    """Concrete value of an attribute attached to a specific entity."""

    __tablename__ = "attribute_values"
    __table_args__ = (
        UniqueConstraint(
            "attribute_id", "entity_type", "entity_id",
            name="uq_attr_entity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    attribute_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("attributes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "workspace" | "user_workspace"
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # The id of the workspace (for workspace scope) or workspace_member
    # (for user scope).
    entity_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    # Stored as JSONB so we can hold any of the supported data_types
    # without per-type columns. Frontend casts based on the attribute's
    # data_type.
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
