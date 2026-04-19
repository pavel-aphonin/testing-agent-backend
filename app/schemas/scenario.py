"""Pydantic schemas for test scenarios."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScenarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    steps_json: dict
    is_active: bool
    created_by_user_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    created_at: datetime


class ScenarioCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    steps_json: dict
    workspace_id: uuid.UUID | None = None


class ScenarioUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    steps_json: dict | None = None
    is_active: bool | None = None
