"""Schemas for notification types + per-workspace settings."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    color: str
    icon: str
    template: str | None
    is_system: bool
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    created_at: datetime
    updated_at: datetime | None


class NotificationTypeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    color: str = Field(default="#888888", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="Bell", max_length=50)
    template: str | None = None
    parent_id: uuid.UUID | None = None
    is_group: bool = False


class NotificationTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str | None = None
    template: str | None = None
    parent_id: uuid.UUID | None = None


class WorkspaceNotificationSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    notification_type_id: uuid.UUID
    is_enabled: bool


class WorkspaceNotificationSettingUpsert(BaseModel):
    notification_type_id: uuid.UUID
    is_enabled: bool
