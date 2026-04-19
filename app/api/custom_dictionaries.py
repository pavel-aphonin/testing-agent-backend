"""/api/custom-dictionaries — per-workspace dictionaries + items.

Members of a workspace can read its dictionaries. Editing requires
``dictionaries.create/edit/delete`` system permission OR moderator/owner
role within the workspace.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.custom_dictionary import (
    CustomDictionary,
    CustomDictionaryItem,
    CustomDictionaryPermission,
)
from app.models.user import User
from app.models.workspace import WorkspaceMember, WsRole
from app.schemas.custom_dictionary import (
    CustomDictionaryCreate,
    CustomDictionaryItemCreate,
    CustomDictionaryItemRead,
    CustomDictionaryItemUpdate,
    CustomDictionaryPermissionRead,
    CustomDictionaryPermissionUpsert,
    CustomDictionaryRead,
    CustomDictionaryUpdate,
)

router = APIRouter(prefix="/api/custom-dictionaries", tags=["custom-dictionaries"])


def _has_perm(user: User, perm: str) -> bool:
    return perm in (user.permissions or [])


async def _check_can_read_ws(
    workspace_id: UUID, user: User, session: AsyncSession
) -> None:
    if _has_perm(user, "users.view"):
        return  # system admin
    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if res.scalar_one_or_none() is None:
        raise HTTPException(403, "Not a member of this workspace")


async def _check_can_edit_ws(
    workspace_id: UUID, user: User, session: AsyncSession
) -> None:
    if _has_perm(user, "users.view"):
        return
    res = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    member = res.scalar_one_or_none()
    if member is None or member.role not in (WsRole.MODERATOR.value, WsRole.OWNER.value):
        raise HTTPException(403, "Requires moderator or owner role")


async def _check_can_view_dict(
    d: CustomDictionary, user: User, session: AsyncSession
) -> None:
    """Read access check that respects per-dictionary ACL.

    If is_restricted=False → workspace membership is enough.
    If is_restricted=True  → user must have a permission row with can_view.
    System admins bypass.
    """
    if _has_perm(user, "users.view"):
        return
    if not d.is_restricted:
        await _check_can_read_ws(d.workspace_id, user, session)
        return
    res = await session.execute(
        select(CustomDictionaryPermission).where(
            CustomDictionaryPermission.dictionary_id == d.id,
            CustomDictionaryPermission.user_id == user.id,
        )
    )
    perm = res.scalar_one_or_none()
    if perm is None or not perm.can_view:
        raise HTTPException(403, "Доступ к этому справочнику ограничен")


async def _check_can_edit_dict(
    d: CustomDictionary, user: User, session: AsyncSession
) -> None:
    """Edit access check that respects per-dictionary ACL.

    is_restricted=False → workspace moderator/owner.
    is_restricted=True  → user must have can_edit row OR be a moderator/owner
                          with explicit can_edit access (not implicit by role).
    """
    if _has_perm(user, "users.view"):
        return
    if not d.is_restricted:
        await _check_can_edit_ws(d.workspace_id, user, session)
        return
    res = await session.execute(
        select(CustomDictionaryPermission).where(
            CustomDictionaryPermission.dictionary_id == d.id,
            CustomDictionaryPermission.user_id == user.id,
        )
    )
    perm = res.scalar_one_or_none()
    if perm is None or not perm.can_edit:
        raise HTTPException(403, "Нет прав на редактирование этого справочника")


# ── Dictionaries ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[CustomDictionaryRead])
async def list_dictionaries(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID,
) -> list[CustomDictionary]:
    await _check_can_read_ws(workspace_id, user, session)
    result = await session.execute(
        select(CustomDictionary)
        .where(CustomDictionary.workspace_id == workspace_id)
        .order_by(CustomDictionary.name)
    )
    all_dicts = list(result.scalars().all())
    # Filter restricted ones the user has no view perm on
    if _has_perm(user, "users.view"):
        return all_dicts
    visible: list[CustomDictionary] = []
    for d in all_dicts:
        if not d.is_restricted:
            visible.append(d)
            continue
        perm_res = await session.execute(
            select(CustomDictionaryPermission).where(
                CustomDictionaryPermission.dictionary_id == d.id,
                CustomDictionaryPermission.user_id == user.id,
            )
        )
        p = perm_res.scalar_one_or_none()
        if p and p.can_view:
            visible.append(d)
    return visible


@router.post("", response_model=CustomDictionaryRead, status_code=201)
async def create_dictionary(
    payload: CustomDictionaryCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> CustomDictionary:
    await _check_can_edit_ws(payload.workspace_id, user, session)

    # code unique within workspace
    exists = await session.execute(
        select(CustomDictionary).where(
            CustomDictionary.workspace_id == payload.workspace_id,
            CustomDictionary.code == payload.code,
        )
    )
    if exists.scalar_one_or_none():
        raise HTTPException(409, "Dictionary with this code already exists in workspace")

    if payload.parent_id:
        parent = await session.get(CustomDictionary, payload.parent_id)
        if parent is None or parent.workspace_id != payload.workspace_id:
            raise HTTPException(404, "Parent not in this workspace")

    d = CustomDictionary(
        workspace_id=payload.workspace_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        is_restricted=payload.is_restricted,
        parent_id=payload.parent_id,
        is_group=payload.is_group,
        created_by_user_id=user.id,
    )
    session.add(d)
    await session.flush()

    # If restricted, automatically grant the creator full access
    if payload.is_restricted:
        session.add(CustomDictionaryPermission(
            dictionary_id=d.id,
            user_id=user.id,
            can_view=True,
            can_edit=True,
        ))
    await session.commit()
    await session.refresh(d)
    return d


@router.patch("/{dict_id}", response_model=CustomDictionaryRead)
async def update_dictionary(
    dict_id: UUID,
    payload: CustomDictionaryUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> CustomDictionary:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_ws(d.workspace_id, user, session)

    if payload.name is not None:
        d.name = payload.name
    if payload.description is not None:
        d.description = payload.description
    if payload.is_restricted is not None:
        d.is_restricted = payload.is_restricted
    if payload.parent_id is not None:
        if payload.parent_id == d.id:
            raise HTTPException(400, "Cannot be own parent")
        parent = await session.get(CustomDictionary, payload.parent_id)
        if parent is None or parent.workspace_id != d.workspace_id:
            raise HTTPException(404, "Parent not in this workspace")
        # Cycle check
        cursor_id = payload.parent_id
        for _ in range(100):
            if cursor_id is None:
                break
            if cursor_id == d.id:
                raise HTTPException(400, "Cycle detected")
            cursor = await session.get(CustomDictionary, cursor_id)
            if cursor is None:
                break
            cursor_id = cursor.parent_id
        d.parent_id = payload.parent_id

    await session.commit()
    await session.refresh(d)
    return d


@router.delete("/{dict_id}", status_code=204)
async def delete_dictionary(
    dict_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_ws(d.workspace_id, user, session)
    await session.delete(d)
    await session.commit()


# ── Items ────────────────────────────────────────────────────────────────────

@router.get("/{dict_id}/items", response_model=list[CustomDictionaryItemRead])
async def list_items(
    dict_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[CustomDictionaryItem]:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_view_dict(d, user, session)

    result = await session.execute(
        select(CustomDictionaryItem)
        .where(CustomDictionaryItem.dictionary_id == dict_id)
        .order_by(CustomDictionaryItem.sort_order, CustomDictionaryItem.name)
    )
    return list(result.scalars().all())


@router.post("/{dict_id}/items", response_model=CustomDictionaryItemRead, status_code=201)
async def create_item(
    dict_id: UUID,
    payload: CustomDictionaryItemCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> CustomDictionaryItem:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_dict(d, user, session)

    # Linear dictionaries don't allow parent_id or groups
    if d.kind == "linear" and (payload.parent_id is not None or payload.is_group):
        raise HTTPException(
            400,
            "Линейный справочник не поддерживает группы и вложенность",
        )

    if payload.parent_id:
        parent = await session.get(CustomDictionaryItem, payload.parent_id)
        if parent is None or parent.dictionary_id != dict_id:
            raise HTTPException(404, "Parent item not in this dictionary")

    item = CustomDictionaryItem(
        dictionary_id=dict_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        parent_id=payload.parent_id,
        is_group=payload.is_group,
        sort_order=payload.sort_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=CustomDictionaryItemRead)
async def update_item(
    item_id: UUID,
    payload: CustomDictionaryItemUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> CustomDictionaryItem:
    item = await session.get(CustomDictionaryItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    d = await session.get(CustomDictionary, item.dictionary_id)
    await _check_can_edit_dict(d, user, session)

    if payload.code is not None:
        item.code = payload.code
    if payload.name is not None:
        item.name = payload.name
    if payload.description is not None:
        item.description = payload.description
    if payload.sort_order is not None:
        item.sort_order = payload.sort_order
    if payload.parent_id is not None:
        if d.kind == "linear":
            raise HTTPException(400, "Линейный справочник не поддерживает вложенность")
        if payload.parent_id == item.id:
            raise HTTPException(400, "Cannot be own parent")
        # Cycle check
        cursor_id = payload.parent_id
        for _ in range(100):
            if cursor_id is None:
                break
            if cursor_id == item.id:
                raise HTTPException(400, "Cycle detected")
            cursor = await session.get(CustomDictionaryItem, cursor_id)
            if cursor is None:
                break
            cursor_id = cursor.parent_id
        item.parent_id = payload.parent_id

    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    item = await session.get(CustomDictionaryItem, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    d = await session.get(CustomDictionary, item.dictionary_id)
    await _check_can_edit_dict(d, user, session)
    await session.delete(item)
    await session.commit()


# ── ACL ──────────────────────────────────────────────────────────────────────

@router.get("/{dict_id}/permissions", response_model=list[CustomDictionaryPermissionRead])
async def list_permissions(
    dict_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_ws(d.workspace_id, user, session)

    res = await session.execute(
        select(CustomDictionaryPermission).where(
            CustomDictionaryPermission.dictionary_id == dict_id
        )
    )
    perms = res.scalars().all()
    out = []
    for p in perms:
        u = await session.get(User, p.user_id)
        out.append({
            "id": p.id,
            "dictionary_id": p.dictionary_id,
            "user_id": p.user_id,
            "user_email": u.email if u else "",
            "can_view": p.can_view,
            "can_edit": p.can_edit,
            "created_at": p.created_at,
        })
    return out


@router.put("/{dict_id}/permissions", response_model=CustomDictionaryPermissionRead)
async def upsert_permission(
    dict_id: UUID,
    payload: CustomDictionaryPermissionUpsert,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_ws(d.workspace_id, user, session)

    target = await session.get(User, payload.user_id)
    if target is None:
        raise HTTPException(404, "User not found")

    res = await session.execute(
        select(CustomDictionaryPermission).where(
            CustomDictionaryPermission.dictionary_id == dict_id,
            CustomDictionaryPermission.user_id == payload.user_id,
        )
    )
    existing = res.scalar_one_or_none()
    if existing is None:
        existing = CustomDictionaryPermission(
            dictionary_id=dict_id,
            user_id=payload.user_id,
            can_view=payload.can_view,
            can_edit=payload.can_edit,
        )
        session.add(existing)
    else:
        existing.can_view = payload.can_view
        existing.can_edit = payload.can_edit

    await session.commit()
    await session.refresh(existing)
    return {
        "id": existing.id,
        "dictionary_id": existing.dictionary_id,
        "user_id": existing.user_id,
        "user_email": target.email,
        "can_view": existing.can_view,
        "can_edit": existing.can_edit,
        "created_at": existing.created_at,
    }


@router.delete("/{dict_id}/permissions/{user_id}", status_code=204)
async def remove_permission(
    dict_id: UUID,
    user_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    d = await session.get(CustomDictionary, dict_id)
    if d is None:
        raise HTTPException(404, "Dictionary not found")
    await _check_can_edit_ws(d.workspace_id, user, session)

    res = await session.execute(
        select(CustomDictionaryPermission).where(
            CustomDictionaryPermission.dictionary_id == dict_id,
            CustomDictionaryPermission.user_id == user_id,
        )
    )
    p = res.scalar_one_or_none()
    if p is not None:
        await session.delete(p)
        await session.commit()
