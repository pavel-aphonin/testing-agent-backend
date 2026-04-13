"""Pydantic schemas for self-service profile endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ProfileRead(BaseModel):
    """The user looking at their own profile page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    is_verified: bool
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    """User-initiated password change. Requires the current password to defend
    against drive-by attacks via stolen JWTs."""

    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)
