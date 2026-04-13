"""Pydantic schemas for test data key-value pairs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TestDataRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    value: str
    category: str
    description: str | None
    created_by_user_id: uuid.UUID
    created_at: datetime


class TestDataCreate(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    category: str = Field(default="general", max_length=50)
    description: str | None = None


class TestDataUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=200)
    value: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = None
