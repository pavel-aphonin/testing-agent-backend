"""Schemas for custom (per-workspace) dictionaries + items."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomDictionaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    code: str
    name: str
    description: str | None
    kind: str  # "linear" | "hierarchical"
    is_restricted: bool = False
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None


class CustomDictionaryCreate(BaseModel):
    workspace_id: uuid.UUID
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    kind: str = Field(default="linear", pattern=r"^(linear|hierarchical)$")
    is_restricted: bool = False
    parent_id: uuid.UUID | None = None
    is_group: bool = False


class CustomDictionaryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_restricted: bool | None = None
    parent_id: uuid.UUID | None = None


class CustomDictionaryPermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dictionary_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str = ""
    can_view: bool
    can_edit: bool
    created_at: datetime


class CustomDictionaryPermissionUpsert(BaseModel):
    user_id: uuid.UUID
    can_view: bool = True
    can_edit: bool = False


class CustomDictionaryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dictionary_id: uuid.UUID
    code: str | None
    name: str
    description: str | None
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    sort_order: int
    created_at: datetime
    updated_at: datetime | None


class CustomDictionaryItemCreate(BaseModel):
    code: str | None = Field(default=None, max_length=100)
    name: str = Field(..., min_length=1, max_length=300)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    sort_order: int = 0


class CustomDictionaryItemUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    description: str | None = None
    parent_id: uuid.UUID | None = None
    sort_order: int | None = None
