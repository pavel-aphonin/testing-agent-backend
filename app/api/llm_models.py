"""LLM model registry endpoints.

Two routers live in this file because they share the same data model but
have very different audiences and permissions:

    /api/admin/models   — admin-only CRUD. The admin uploads a GGUF file
                          to the llm volume out-of-band, then registers
                          its metadata here so testers can pick it.

    /api/models         — read-only list of *active* models for testers
                          and viewers. Returns a slim public schema with
                          no file paths or upload provenance.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import require_admin, require_viewer
from app.db import get_async_session
from app.llm_swap import regenerate_swap_config
from app.models.llm_model import LLMModel
from app.models.user import User
from app.schemas.llm_model import (
    LLMModelCreate,
    LLMModelPublicRead,
    LLMModelRead,
    LLMModelUpdate,
)
import logging

logger = logging.getLogger(__name__)


async def _regenerate_swap_safely(session: AsyncSession) -> None:
    """Best-effort regenerate of llama-swap.yaml after a CRUD change.

    Failures here (read-only mount, missing dir, disk full) must NOT roll
    back the database change. The admin still wants the model registered;
    they can manually re-trigger the regeneration with another PATCH.
    """
    try:
        await regenerate_swap_config(session)
    except Exception:
        logger.exception("Failed to regenerate llama-swap.yaml")

admin_router = APIRouter(prefix="/api/admin/models", tags=["admin-models"])
public_router = APIRouter(prefix="/api/models", tags=["models"])


# --------------------------------------------------------------- admin CRUD


@admin_router.get("", response_model=list[LLMModelRead])
async def list_all_models(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[LLMModel]:
    result = await session.execute(select(LLMModel).order_by(LLMModel.name))
    return list(result.scalars().all())


@admin_router.post(
    "",
    response_model=LLMModelRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    payload: LLMModelCreate,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> LLMModel:
    model = LLMModel(
        name=payload.name,
        description=payload.description,
        family=payload.family,
        gguf_path=payload.gguf_path,
        mmproj_path=payload.mmproj_path,
        size_bytes=payload.size_bytes,
        context_length=payload.context_length,
        quantization=payload.quantization,
        supports_vision=payload.supports_vision,
        supports_tool_use=payload.supports_tool_use,
        default_temperature=payload.default_temperature,
        default_top_p=payload.default_top_p,
        is_active=payload.is_active,
        notes=payload.notes,
        uploaded_by_user_id=admin.id,
    )
    session.add(model)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A model with this name already exists",
        ) from exc
    await session.refresh(model)
    await _regenerate_swap_safely(session)
    return model


@admin_router.get("/{model_id}", response_model=LLMModelRead)
async def get_model(
    model_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> LLMModel:
    result = await session.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


@admin_router.patch("/{model_id}", response_model=LLMModelRead)
async def update_model(
    model_id: UUID,
    payload: LLMModelUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> LLMModel:
    result = await session.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    # Apply only the fields that were actually provided.
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(model, field, value)

    await session.commit()
    await session.refresh(model)
    await _regenerate_swap_safely(session)
    return model


@admin_router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    await session.delete(model)
    await session.commit()
    await _regenerate_swap_safely(session)


# ----------------------------------------------------------------- public list


@public_router.get("", response_model=list[LLMModelPublicRead])
async def list_active_models(
    _user: Annotated[User, Depends(require_viewer)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[LLMModel]:
    """Return active models only — what testers see in the New Run dropdown."""
    result = await session.execute(
        select(LLMModel)
        .where(LLMModel.is_active.is_(True))
        .order_by(LLMModel.family, LLMModel.name)
    )
    return list(result.scalars().all())
