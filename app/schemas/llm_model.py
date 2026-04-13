"""Pydantic schemas for the LLM model registry.

Two read models live side by side:
    LLMModelRead       — full record for the admin UI
    LLMModelPublicRead — slim view for the New Run dropdown (testers and viewers
                          shouldn't see file paths or upload provenance)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LLMModelCreate(BaseModel):
    """Admin form: register a new GGUF model."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    family: str = Field(..., min_length=1, max_length=50)
    gguf_path: str = Field(..., min_length=1)
    mmproj_path: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    context_length: int = Field(default=4096, ge=128, le=1_000_000)
    quantization: str = Field(..., min_length=1, max_length=20)
    supports_vision: bool = False
    supports_tool_use: bool = False
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    is_active: bool = True
    notes: str | None = None


class LLMModelUpdate(BaseModel):
    """Admin form: update any subset of model fields."""

    description: str | None = None
    is_active: bool | None = None
    default_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    benchmark_tps: float | None = Field(default=None, ge=0.0)
    benchmark_ttft_ms: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


class LLMModelRead(BaseModel):
    """Full admin view including paths and provenance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    family: str
    gguf_path: str
    mmproj_path: str | None
    size_bytes: int
    context_length: int
    quantization: str
    supports_vision: bool
    supports_tool_use: bool
    default_temperature: float
    default_top_p: float
    benchmark_tps: float | None
    benchmark_ttft_ms: float | None
    is_active: bool
    uploaded_by_user_id: uuid.UUID | None
    uploaded_at: datetime
    notes: str | None


class LLMModelPublicRead(BaseModel):
    """Slim public view used by testers when picking a model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    family: str
    description: str | None
    context_length: int
    quantization: str
    supports_vision: bool
    supports_tool_use: bool
