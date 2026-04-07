"""Pydantic schemas for the Run resource."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.run import RunMode


class RunCreate(BaseModel):
    bundle_id: str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(default="ios", max_length=20)
    mode: str = Field(default=RunMode.HYBRID.value)
    max_steps: int = Field(default=200, ge=1, le=10000)
    c_puct: float = Field(default=2.0, ge=0.0, le=10.0)
    rollout_depth: int = Field(default=5, ge=0, le=100)


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    bundle_id: str
    device_id: str
    platform: str
    mode: str
    status: str
    max_steps: int
    c_puct: float
    rollout_depth: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    stats_json: dict | None = None
