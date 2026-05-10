"""/api/scenario-shapes — admin-extensible palette of scenario nodes.

GET is open to any authenticated user (the editor needs the list to
render the palette); writes are admin-only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_admin
from app.db import get_async_session
from app.models.scenario_shape import ScenarioShape
from app.models.user import User
from app.schemas.scenario_shape import (
    ScenarioShapeCreate,
    ScenarioShapeRead,
    ScenarioShapeUpdate,
)

router = APIRouter(prefix="/api/scenario-shapes", tags=["scenario-shapes"])


@router.get("", response_model=list[ScenarioShapeRead])
async def list_shapes(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[ScenarioShape]:
    """List every shape, ordered by sort_order then name. The editor
    consumes this in one shot — the dictionary is small (~10 entries
    typical, ~50 worst case) and rarely changes, so we don't paginate."""
    result = await session.execute(
        select(ScenarioShape).order_by(
            ScenarioShape.sort_order.asc(), ScenarioShape.name.asc()
        )
    )
    return list(result.scalars().all())


@router.post(
    "",
    response_model=ScenarioShapeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_shape(
    payload: ScenarioShapeCreate,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ScenarioShape:
    # Code uniqueness is enforced at the DB level too — this is just
    # a friendlier error than the IntegrityError the constraint would
    # otherwise raise.
    existing = (
        await session.execute(
            select(ScenarioShape).where(ScenarioShape.code == payload.code)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Shape with code {payload.code!r} already exists",
        )
    shape = ScenarioShape(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        geometry=payload.geometry,
        color=payload.color,
        icon=payload.icon,
        action_code=payload.action_code,
        attributes=payload.attributes,
        sort_order=payload.sort_order,
        is_builtin=False,
    )
    session.add(shape)
    await session.commit()
    await session.refresh(shape)
    return shape


@router.patch(
    "/{shape_id}",
    response_model=ScenarioShapeRead,
    dependencies=[Depends(require_admin)],
)
async def update_shape(
    shape_id: UUID,
    payload: ScenarioShapeUpdate,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ScenarioShape:
    shape = (
        await session.execute(
            select(ScenarioShape).where(ScenarioShape.id == shape_id)
        )
    ).scalar_one_or_none()
    if shape is None:
        raise HTTPException(status_code=404, detail="Shape not found")

    patch = payload.model_dump(exclude_unset=True)

    # Built-ins lock down the structural fields the worker dispatches
    # on. Admins can still rename, recolour, swap icon, edit the
    # attribute schema, change action_code etc.
    if shape.is_builtin:
        for locked in ("code", "category", "geometry"):
            if locked in patch and patch[locked] != getattr(shape, locked):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Field {locked!r} is locked on built-in shapes. "
                        "Create a copy with a different code if you need "
                        "different runtime semantics."
                    ),
                )
        patch.pop("code", None)
        patch.pop("category", None)
        patch.pop("geometry", None)

    for k, v in patch.items():
        setattr(shape, k, v)
    await session.commit()
    await session.refresh(shape)
    return shape


@router.delete(
    "/{shape_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_shape(
    shape_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    shape = (
        await session.execute(
            select(ScenarioShape).where(ScenarioShape.id == shape_id)
        )
    ).scalar_one_or_none()
    if shape is None:
        raise HTTPException(status_code=404, detail="Shape not found")
    if shape.is_builtin:
        raise HTTPException(
            status_code=400,
            detail="Built-in shapes cannot be deleted (only edited).",
        )
    await session.delete(shape)
    await session.commit()
