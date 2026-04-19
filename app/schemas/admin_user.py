"""Pydantic schemas for admin user management."""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminUserCreate(BaseModel):
    """Admin form: create a user and assign a role from the roles table."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    role_id: uuid.UUID
    must_change_password: bool = True


class AdminUserUpdate(BaseModel):
    """Admin can change role, reactivate, or reset must_change_password."""

    role_id: uuid.UUID | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    # Legacy string for backward compat
    role: str
    # New RBAC fields
    role_id: uuid.UUID | None
    role_name: str
    role_code: str
    permissions: list[str]
    is_active: bool
    is_superuser: bool
    is_verified: bool
    must_change_password: bool
