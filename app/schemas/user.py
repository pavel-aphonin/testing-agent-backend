"""Pydantic schemas for the User resource."""

import uuid

from fastapi_users import schemas

from app.models.user import UserRole


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: str
    must_change_password: bool


class UserCreate(schemas.BaseUserCreate):
    role: str = UserRole.TESTER.value
    must_change_password: bool = True


class UserUpdate(schemas.BaseUserUpdate):
    role: str | None = None
    must_change_password: bool | None = None
