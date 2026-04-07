"""/api/runs — list, create, fetch, delete exploration runs.

Permission rules:
    - viewer/tester/admin can list and get their own runs
    - admin can list and get any run
    - tester/admin can create runs (viewer cannot)
    - admin can delete any run; tester only their own; viewer cannot delete
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_tester
from app.db import get_async_session
from app.models.run import Run, RunStatus
from app.models.user import User, UserRole
from app.schemas.run import RunCreate, RunRead

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


@router.get("", response_model=list[RunRead])
async def list_runs(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[Run]:
    if _is_admin(user):
        result = await session.execute(select(Run).order_by(Run.created_at.desc()))
    else:
        result = await session.execute(
            select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc())
        )
    return list(result.scalars().all())


@router.post(
    "",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tester)],
)
async def create_run(
    payload: RunCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Create a new exploration run in `pending` status.

    The actual exploration is started by Block 4 (subprocess runner).
    For now this endpoint just records the request so the UI can list it.
    """
    run = Run(
        user_id=user.id,
        bundle_id=payload.bundle_id,
        device_id=payload.device_id,
        platform=payload.platform,
        mode=payload.mode,
        max_steps=payload.max_steps,
        c_puct=payload.c_puct,
        rollout_depth=payload.rollout_depth,
        status=RunStatus.PENDING.value,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _is_admin(user) and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    return run


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _is_admin(user) and run.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your run")
    if user.role == UserRole.VIEWER.value:
        raise HTTPException(status_code=403, detail="Viewers cannot delete runs")
    await session.delete(run)
    await session.commit()
