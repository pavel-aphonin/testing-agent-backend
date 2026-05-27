"""PER-193: Pydantic schemas for per-role LLM assignment.

``ModuleAssignmentRead`` joins the assignment row with a thin
projection of the assigned ``LLMModel`` so the admin UI can render
«Planner → Qwen3-VL-32B (4.8 GB, supports_vision)» in one row
without a second round-trip.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.module_assignment import ModuleRole


class AssignedModelBrief(BaseModel):
    """Compact projection of LLMModel for embedding in assignment rows.

    Intentionally tiny — full LLMModel has ~30 columns including
    thinking passport, sampling defaults, benchmarks. None of that
    is needed in the role picker. If the admin UI needs more, it
    can fetch the full model via /api/admin/models/{id}.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    family: str
    quantization: str
    size_bytes: int
    supports_vision: bool
    is_active: bool
    supported_roles: list[str] = Field(default_factory=list)


class ModuleAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: ModuleRole
    llm_model_id: UUID | None
    llm_model: AssignedModelBrief | None
    assigned_at: datetime | None
    assigned_by_user_id: UUID | None
    updated_at: datetime


class ModuleAssignmentUpsert(BaseModel):
    """PUT body — assign or unassign a model on a role.

    ``llm_model_id=None`` is the «unassign» path. The endpoint
    overwrites whatever was there, sets ``assigned_at=now()`` and
    ``assigned_by_user_id`` to the caller. No PATCH variant — there
    are only two interesting fields and one of them is always set
    by the server.
    """

    llm_model_id: UUID | None
