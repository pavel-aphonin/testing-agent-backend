"""Pydantic schemas for defects."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DefectRead(BaseModel):
    """List-view projection for the Defects page in the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_idx: int | None
    screen_id_hash: str | None
    screen_name: str | None
    priority: str
    kind: str
    title: str
    description: str
    screenshot_path: str | None
    external_ticket_id: str | None
    created_at: datetime


class DefectCreate(BaseModel):
    """Worker-posted defect. Agent fills this from LLM analysis of an observed failure."""

    run_id: uuid.UUID
    step_idx: int | None = None
    screen_id_hash: str | None = Field(default=None, max_length=64)
    screen_name: str | None = Field(default=None, max_length=500)
    priority: str = Field(default="P2", pattern="^P[0-3]$")
    kind: str = Field(default="functional")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1)
    screenshot_path: str | None = None
    llm_analysis_json: dict | None = None
