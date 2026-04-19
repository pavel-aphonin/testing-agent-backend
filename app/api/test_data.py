"""/api/test-data — CRUD for test data key-value pairs.

Permission rules:
    - any authenticated user can list test data
    - tester/admin can create, update, and delete
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_tester
from app.db import get_async_session
from app.models.test_data import TestData
from app.models.user import User
from app.schemas.test_data import TestDataCreate, TestDataRead, TestDataUpdate

router = APIRouter(prefix="/api/test-data", tags=["test-data"])


@router.get("", response_model=list[TestDataRead])
async def list_test_data(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
) -> list[TestData]:
    """List test data entries. Filtered by workspace if provided."""
    q = select(TestData)
    if workspace_id is not None:
        q = q.where(TestData.workspace_id == workspace_id)
    q = q.order_by(TestData.category, TestData.key)
    result = await session.execute(q)
    return list(result.scalars().all())


@router.post(
    "",
    response_model=TestDataRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tester)],
)
async def create_test_data(
    payload: TestDataCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> TestData:
    entry = TestData(
        key=payload.key,
        value=payload.value,
        category=payload.category,
        description=payload.description,
        created_by_user_id=user.id,
        workspace_id=payload.workspace_id,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


@router.patch(
    "/{entry_id}",
    response_model=TestDataRead,
    dependencies=[Depends(require_tester)],
)
async def update_test_data(
    entry_id: UUID,
    payload: TestDataUpdate,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> TestData:
    result = await session.execute(
        select(TestData).where(TestData.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Test data entry not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)

    await session.commit()
    await session.refresh(entry)
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_tester)],
)
async def delete_test_data(
    entry_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(TestData).where(TestData.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Test data entry not found")
    await session.delete(entry)
    await session.commit()
