"""Pydantic schemas for workspaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    logo_path: str | None
    is_archived: bool
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None


class WorkspaceCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    is_group: bool = False


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    parent_id: uuid.UUID | None = None


class WorkspaceMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str = ""
    role: str
    joined_at: datetime


class WorkspaceMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: str = "member"


class WorkspaceBrief(BaseModel):
    """Minimal workspace info for the switcher dropdown."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    logo_path: str | None
