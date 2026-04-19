"""User model. Extends fastapi-users base with role and first-login fields."""

from __future__ import annotations

import uuid

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    """A human user of the Testing Agent platform.

    Inherited from fastapi-users:
        id: UUID
        email: str (unique)
        hashed_password: str
        is_active: bool
        is_superuser: bool
        is_verified: bool
    """

    __tablename__ = "users"

    # FK to the roles table. Nullable temporarily during migration, but
    # in production every user must have a role.
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Keep the legacy string column so old sessions / tokens that reference
    # ``user.role`` don't crash immediately. New code should read from the
    # relationship. The migration backfills this from the roles table.
    role: Mapped[str] = mapped_column(
        String(20),
        default="tester",
        nullable=False,
    )

    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Eager-load role so permission checks don't need an extra query.
    role_obj: Mapped["Role"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Role", lazy="joined", foreign_keys=[role_id],
    )

    @property
    def permissions(self) -> list[str]:
        """Return the flat permission list from the linked Role."""
        if self.role_obj is not None:
            return self.role_obj.permissions
        return []

    @property
    def role_name(self) -> str:
        if self.role_obj is not None:
            return self.role_obj.name
        return self.role

    @property
    def role_code(self) -> str:
        if self.role_obj is not None:
            return self.role_obj.code
        return self.role
