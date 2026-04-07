"""Pydantic schemas for admin user management."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class AdminUserCreate(BaseModel):
    """Admin form: create a tester or viewer (or another admin)."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    role: str = Field(default=UserRole.TESTER.value)
    must_change_password: bool = True


class AdminUserUpdate(BaseModel):
    """Admin can change role, reactivate, or reset must_change_password."""

    role: str | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    must_change_password: bool
