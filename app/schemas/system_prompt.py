"""Pydantic schemas for system prompts (PER-111).

The admin API exposes one resource — system prompts — for read/update/
reset. Worker-side code talks to a dedicated GET endpoint that returns
``SystemPromptRead`` so the prompt content arrives without the cosmetic
fields (``name``, ``description``) that only the UI cares about.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SystemPromptRead(BaseModel):
    """Full prompt row — what the admin UI lists / what the worker
    fetches at render time."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    content: str
    default_content: str
    placeholders: list[str]
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


class SystemPromptUpdate(BaseModel):
    """Admin edit payload. Only the mutable fields are accepted —
    ``code`` / ``placeholders`` / ``default_content`` are fixed by the
    seed migration and cannot change at runtime, otherwise the worker
    would break on the next render."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, min_length=1)
