"""Pydantic schemas for the User resource."""

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: str
    must_change_password: bool
    # New RBAC fields — populated from the Role relationship.
    role_id: uuid.UUID | None = None
    role_name: str = ""
    role_code: str = ""
    permissions: list[str] = []
    # Relative path to uploaded avatar. None = frontend renders default circle.
    avatar_path: str | None = None


class UserCreate(schemas.BaseUserCreate):
    role: str = "tester"
    must_change_password: bool = True


class UserUpdate(schemas.BaseUserUpdate):
    role: str | None = None
    must_change_password: bool | None = None
