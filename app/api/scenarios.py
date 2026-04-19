"""/api/scenarios — CRUD for reusable test scenarios.

Permission rules:
    - any authenticated user can list active scenarios and get one by id
    - tester/admin can create, update, and delete scenarios
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_tester
from app.db import get_async_session
from app.models.scenario import Scenario
from app.models.user import User
from app.schemas.scenario import ScenarioCreate, ScenarioRead, ScenarioUpdate

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("", response_model=list[ScenarioRead])
async def list_scenarios(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
) -> list[Scenario]:
    """List active scenarios. Filtered by workspace if provided."""
    q = select(Scenario).where(Scenario.is_active.is_(True))
    if workspace_id is not None:
        q = q.where(Scenario.workspace_id == workspace_id)
    q = q.order_by(Scenario.created_at.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


@router.post(
    "",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tester)],
)
async def create_scenario(
    payload: ScenarioCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    scenario = Scenario(
        title=payload.title,
        description=payload.description,
        steps_json=payload.steps_json,
        created_by_user_id=user.id,
        workspace_id=payload.workspace_id,
    )
    session.add(scenario)
    await session.commit()
    await session.refresh(scenario)
    return scenario


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(
    scenario_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


@router.patch(
    "/{scenario_id}",
    response_model=ScenarioRead,
    dependencies=[Depends(require_tester)],
)
async def update_scenario(
    scenario_id: UUID,
    payload: ScenarioUpdate,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(scenario, field, value)

    await session.commit()
    await session.refresh(scenario)
    return scenario


@router.delete(
    "/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_tester)],
)
async def delete_scenario(
    scenario_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await session.delete(scenario)
    await session.commit()
