"""PER-195: /api/internal/module-assignments — worker-side resolver.

Mirrors the admin endpoint but:
    * Gated by ``WORKER_TOKEN`` instead of JWT (same pattern as
      ``app/api/internal_runs.py``).
    * Returns a flat passport for the currently-assigned model
      (endpoint URL, model name, sampling defaults, vision flag,
      etc.) so the worker doesn't need to chain a second call to
      ``/api/admin/models/{id}``.
    * Returns 404 when the role is unassigned — worker decides
      whether that's fatal (PLANNER missing) or fine (GROUNDING_VERIFIER
      missing means logprobs-only mode).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models.llm_model import LLMModel
from app.models.module_assignment import ModuleAssignment, ModuleRole

router = APIRouter(
    prefix="/api/internal/module-assignments",
    tags=["internal"],
)


def require_worker_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Same Bearer-token gate as internal_runs.py.

    Kept inline rather than imported so this router has no surprise
    coupling — if the worker auth scheme changes we update both
    files explicitly.
    """
    expected = f"Bearer {settings.worker_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
        )


@router.get(
    "/{role}",
    dependencies=[Depends(require_worker_token)],
)
async def resolve_role(
    role: ModuleRole,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Return the passport of the LLMModel currently assigned to ``role``.

    Shape is deliberately flat — the worker reads ``endpoint_url`` /
    ``model_name`` directly, doesn't need to navigate nested objects.
    Returns 404 when no model is assigned (the assignment row exists,
    seeded by the migration, but ``llm_model_id`` is NULL).
    """
    result = await session.execute(
        select(ModuleAssignment).where(ModuleAssignment.role == role.value)
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        # Migration should have seeded this — if it didn't, the
        # database is out of date.
        raise HTTPException(
            status_code=500,
            detail=f"Role {role.value} not seeded — run alembic upgrade head",
        )
    if assignment.llm_model_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"Role {role.value} has no assigned model",
        )

    # Fetch the full LLMModel — relationship is lazy=joined so this is
    # one round-trip, but we re-load explicitly for clarity.
    model = await session.get(LLMModel, assignment.llm_model_id)
    if model is None:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Role {role.value} assigned to model_id {assignment.llm_model_id} "
                "but the model row was deleted. Reassign via /admin/module-assignments."
            ),
        )

    return {
        "role": role.value,
        "model_id": str(model.id),
        "model_name": model.name,
        "family": model.family,
        "provider": model.provider,
        "endpoint_url": model.endpoint_url,
        "gguf_path": model.gguf_path,
        "mmproj_path": model.mmproj_path,
        "context_length": model.context_length,
        "supports_vision": model.supports_vision,
        "supports_tool_use": model.supports_tool_use,
        "supports_thinking": model.supports_thinking,
        "supports_json_schema": model.supports_json_schema,
        "thinking_activation": model.thinking_activation,
        "thinking_extract_regex": model.thinking_extract_regex,
        "default_temperature": model.default_temperature,
        "default_top_p": model.default_top_p,
        "default_top_k": model.default_top_k,
        "default_min_p": model.default_min_p,
        "tap_at_coord_space": model.tap_at_coord_space,
        "image_min_tokens": model.image_min_tokens,
        "screenshot_max_dim": model.screenshot_max_dim,
        "supported_roles": list(model.supported_roles or []),
    }
