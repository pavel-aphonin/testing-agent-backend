"""Pydantic schemas for attributes + their values."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AttributeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    data_type: str
    enum_values: list | None
    default_value: Any | None
    scope: str
    applies_to: str
    is_system: bool
    is_required: bool = False
    parent_id: uuid.UUID | None = None
    is_group: bool = False
    created_at: datetime
    updated_at: datetime | None


class AttributeCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    data_type: str = Field(
        ..., pattern=r"^(string|number|boolean|enum|date|link|member)$"
    )
    enum_values: list | None = None
    default_value: Any | None = None
    scope: str = Field(default="workspace", pattern=r"^(workspace|user)$")
    applies_to: str = Field(default="workspace")
    is_required: bool = False
    parent_id: uuid.UUID | None = None
    is_group: bool = False


class AttributeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enum_values: list | None = None
    default_value: Any | None = None
    is_required: bool | None = None
    parent_id: uuid.UUID | None = None


class AttributeValueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attribute_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    value: Any | None
    updated_at: datetime | None


class AttributeValueSet(BaseModel):
    """Upsert value for (attribute, entity)."""

    attribute_id: uuid.UUID
    entity_type: str = Field(..., pattern=r"^(workspace|user_workspace)$")
    entity_id: uuid.UUID
    value: Any | None = None
