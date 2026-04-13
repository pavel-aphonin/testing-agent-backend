"""Pydantic schemas for per-user agent settings."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class AgentSettingsRead(BaseModel):
    """Defaults the New Run modal pre-fills for this user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    default_mode: str
    default_llm_model_id: uuid.UUID | None
    default_max_steps: int
    c_puct: float
    rollout_depth: int
    graph_library: str
    language: str
    vision_model_id: uuid.UUID | None = None
    thinking_model_id: uuid.UUID | None = None
    instruct_model_id: uuid.UUID | None = None
    coder_model_id: uuid.UUID | None = None
    rag_enabled: bool = False


class AgentSettingsUpdate(BaseModel):
    """All fields optional — only the ones provided are written."""

    default_mode: str | None = Field(default=None, pattern="^(mc|ai|hybrid)$")
    default_llm_model_id: uuid.UUID | None = None
    default_max_steps: int | None = Field(default=None, ge=1, le=10_000)
    c_puct: float | None = Field(default=None, ge=0.0, le=10.0)
    rollout_depth: int | None = Field(default=None, ge=0, le=100)
    graph_library: str | None = Field(
        default=None, pattern="^(react-flow|cytoscape|vis-network)$"
    )
    language: str | None = Field(default=None, pattern="^(en|ru)$")
    vision_model_id: uuid.UUID | None = None
    thinking_model_id: uuid.UUID | None = None
    instruct_model_id: uuid.UUID | None = None
    coder_model_id: uuid.UUID | None = None
    rag_enabled: bool | None = None
