"""/api/settings — per-user agent defaults.

Each user has at most one row in agent_settings (unique on user_id). The
GET endpoint creates a default row on first read so the frontend never
has to check for "no settings yet" — it just shows what the API returned.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.agent_settings import AgentSettings
from app.models.llm_model import LLMModel
from app.models.user import User
from app.schemas.agent_settings import AgentSettingsRead, AgentSettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def _get_or_create_settings(
    session: AsyncSession, user_id
) -> AgentSettings:
    result = await session.execute(
        select(AgentSettings).where(AgentSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    # First read for this user — materialize a default row so updates have
    # something to PATCH against. Defaults match the SQL column defaults.
    settings = AgentSettings(user_id=user_id)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


@router.get("", response_model=AgentSettingsRead)
async def get_my_settings(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AgentSettings:
    return await _get_or_create_settings(session, user.id)


@router.patch("", response_model=AgentSettingsRead)
async def update_my_settings(
    payload: AgentSettingsUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AgentSettings:
    settings = await _get_or_create_settings(session, user.id)

    update_data = payload.model_dump(exclude_unset=True)

    # If the user picked an LLM model, make sure it actually exists and is
    # active. Otherwise the New Run modal would silently default to nothing.
    if "default_llm_model_id" in update_data and update_data["default_llm_model_id"] is not None:
        model_id = update_data["default_llm_model_id"]
        result = await session.execute(
            select(LLMModel).where(LLMModel.id == model_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise HTTPException(status_code=404, detail="Selected LLM model not found")
        if not model.is_active:
            raise HTTPException(
                status_code=400,
                detail="Selected LLM model is not active",
            )

    for field, value in update_data.items():
        setattr(settings, field, value)

    await session.commit()
    await session.refresh(settings)
    return settings
