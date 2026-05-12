"""/api/test-data — CRUD for test data key-value pairs.

Permission rules (PER-106 #10):
    - any authenticated user can list test data
    - create requires ``test_data.create``
    - update requires ``test_data.edit``
    - delete requires ``test_data.delete``
Resource-specific permissions instead of the old "tester/admin" role
literal — see app/auth/users.py for the migration notes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.test_data import TestData
from app.models.user import User
from app.schemas.test_data import TestDataCreate, TestDataRead, TestDataUpdate

router = APIRouter(prefix="/api/test-data", tags=["test-data"])


@router.get("", response_model=list[TestDataRead])
async def list_test_data(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
) -> list[TestData]:
    """List test data entries. Filtered by workspace if provided.

    PER-106 #2: requires workspace membership when ``workspace_id`` is
    set — previously any caller could read any workspace's test data
    by passing its id.
    """
    from app.auth.users import require_workspace_membership
    await require_workspace_membership(session, user, workspace_id)
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
    # PER-106 #10: scope-correct permission, not the legacy runs.*
    # tester bundle. Same access for the seeded roles, room for
    # finer-grained custom roles later.
    dependencies=[Depends(require_permission("test_data.create"))],
)
async def create_test_data(
    payload: TestDataCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> TestData:
    # PER-106 #2: enforce workspace ownership on create.
    from app.auth.users import require_workspace_membership
    await require_workspace_membership(session, user, payload.workspace_id)
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
    dependencies=[Depends(require_permission("test_data.edit"))],
)
async def update_test_data(
    entry_id: UUID,
    payload: TestDataUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> TestData:
    result = await session.execute(
        select(TestData).where(TestData.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Test data entry not found")

    # PER-106 #2: enforce workspace ownership on update — both the
    # existing entry's workspace and the new one if the patch tries
    # to move the entry between workspaces.
    from app.auth.users import require_workspace_membership
    await require_workspace_membership(session, user, entry.workspace_id)
    patch = payload.model_dump(exclude_unset=True)
    if "workspace_id" in patch and patch["workspace_id"] != entry.workspace_id:
        await require_workspace_membership(session, user, patch["workspace_id"])

    for field, value in patch.items():
        setattr(entry, field, value)

    await session.commit()
    await session.refresh(entry)
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("test_data.delete"))],
)
async def delete_test_data(
    entry_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(TestData).where(TestData.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Test data entry not found")
    # PER-106 #2: enforce workspace membership on delete.
    from app.auth.users import require_workspace_membership
    await require_workspace_membership(session, user, entry.workspace_id)
    await session.delete(entry)
    await session.commit()
