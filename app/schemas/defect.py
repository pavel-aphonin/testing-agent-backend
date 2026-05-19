"""Pydantic schemas for defects (PER-120 — priority/severity references)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DefectRefRead(BaseModel):
    """Inline read for ref_defect_priorities / ref_defect_severities. Used
    inside :class:`DefectRead` so the UI never has to do a second round-trip
    to display the colour chip + Russian name on every defect row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    color: str
    description: str = ""
    sort_order: int = 0


class DefectRead(BaseModel):
    """List-view projection for the Defects page in the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_idx: int | None
    screen_id_hash: str | None
    screen_name: str | None
    priority: DefectRefRead
    severity: DefectRefRead
    kind: str
    title: str
    description: str
    screenshot_path: str | None
    external_ticket_id: str | None
    # Raw LLM rationale stored by the worker's defect detector. Surfaced
    # in the UI under a "Анализ модели" Collapse so reviewers can audit
    # why the model flagged this. Optional — older detectors and the
    # heuristic fallback don't fill it.
    llm_analysis_json: dict | None = None
    created_at: datetime


class DefectCreate(BaseModel):
    """Worker-posted defect. Agent fills this from LLM analysis of an
    observed failure.

    Priority and severity arrive as the reference ``code`` (e.g.
    ``"urgent"``, ``"blocker"``), not as UUID — the worker has the
    dictionary cached but doesn't track IDs. The backend resolves the
    code to a row on insert; unknown codes fall back to the documented
    default (``medium`` / ``major``) and log a warning rather than 400
    so a misconfigured agent doesn't silently lose defects.
    """

    run_id: uuid.UUID
    step_idx: int | None = None
    screen_id_hash: str | None = Field(default=None, max_length=64)
    screen_name: str | None = Field(default=None, max_length=500)
    priority_code: str = Field(default="medium", max_length=50)
    severity_code: str = Field(default="major", max_length=50)
    kind: str = Field(default="functional")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1)
    screenshot_path: str | None = None
    llm_analysis_json: dict | None = None
