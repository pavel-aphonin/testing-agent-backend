"""Pydantic schemas for the scenario-shapes dictionary (PER-90)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioShapeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    category: str
    geometry: str
    color: str
    icon: str | None
    action_code: str | None
    attributes: list[dict[str, Any]]
    is_builtin: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime | None


class ScenarioShapeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    category: str = Field(min_length=1, max_length=32)
    geometry: str = Field(min_length=1, max_length=32)
    color: str = Field(default="#1677ff", max_length=16)
    icon: str | None = None
    action_code: str | None = None
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    sort_order: int = 100


class ScenarioShapeUpdate(BaseModel):
    # Code/category/geometry are intentionally absent here for
    # built-in shapes — admins can rename and recolour, but the
    # runtime semantics are anchored to those three fields and the
    # worker would mis-execute if they drifted. Custom shapes can
    # still update everything via a dedicated admin endpoint we'll
    # add when there's a real need; for now the lock is conservative.
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    color: str | None = Field(default=None, max_length=16)
    icon: str | None = None
    action_code: str | None = None
    attributes: list[dict[str, Any]] | None = None
    sort_order: int | None = None
    # Custom (non-builtin) shapes additionally allow editing these.
    # The handler enforces the gate on built-ins.
    code: str | None = None
    category: str | None = None
    geometry: str | None = None
