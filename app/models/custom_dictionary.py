"""Per-workspace custom dictionaries.

A custom dictionary belongs to a single workspace. Two kinds:
  - "linear":       a flat list of items (no groups, items can't have parents)
  - "hierarchical": items form a tree (parent_id + is_group, unlimited depth)

Kind is chosen at creation and is immutable.

Custom dictionaries can themselves be grouped (the dictionary list at
the workspace level supports tree structure too).

Items can be referenced from enum-type attributes via
``Attribute.source_dictionary_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CustomDictionary(Base):
    __tablename__ = "custom_dictionaries"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "linear" | "hierarchical"
    kind: Mapped[str] = mapped_column(String(20), default="linear", nullable=False)

    # When false (default): workspace permissions apply — any member can
    # view, moderators can edit.
    # When true: only users listed in custom_dictionary_permissions can
    # access this dictionary, regardless of their workspace role.
    is_restricted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tree of dictionaries within a workspace (optional grouping)
    parent_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("custom_dictionaries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class CustomDictionaryItem(Base):
    __tablename__ = "custom_dictionary_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("custom_dictionaries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tree of items within a HIERARCHICAL dictionary. Always null for linear.
    parent_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("custom_dictionary_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class CustomDictionaryPermission(Base):
    """Per-user ACL entry for a custom dictionary.

    Only consulted when the dictionary's ``is_restricted`` flag is true.
    Absent row = no access. ``can_edit`` implies ``can_view``.
    """

    __tablename__ = "custom_dictionary_permissions"
    __table_args__ = (
        UniqueConstraint("dictionary_id", "user_id", name="uq_cdict_perm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dictionary_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("custom_dictionaries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
