"""User model. Extends fastapi-users base with role and first-login fields."""

from enum import StrEnum

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(StrEnum):
    VIEWER = "viewer"
    TESTER = "tester"
    ADMIN = "admin"


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

    # Override the singular "user" default from SQLAlchemyBaseUserTableUUID
    # so foreign keys can target "users.id" consistently across the schema.
    __tablename__ = "users"

    role: Mapped[str] = mapped_column(
        String(20),
        default=UserRole.TESTER.value,
        nullable=False,
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
