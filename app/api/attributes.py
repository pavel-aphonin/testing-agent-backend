"""/api/dictionaries/attributes — attribute definitions
   /api/attribute-values        — concrete values attached to objects.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.attribute import Attribute, AttributeValue
from app.models.user import User
from app.schemas.attribute import (
    AttributeCreate,
    AttributeRead,
    AttributeUpdate,
    AttributeValueRead,
    AttributeValueSet,
)

attr_router = APIRouter(prefix="/api/dictionaries/attributes", tags=["attributes"])
val_router = APIRouter(prefix="/api/attribute-values", tags=["attribute-values"])


# ── Definitions ──────────────────────────────────────────────────────────────

@attr_router.get("", response_model=list[AttributeRead])
async def list_attributes(
    _user: Annotated[User, Depends(require_permission("dictionaries.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    applies_to: str | None = None,
) -> list[Attribute]:
    q = select(Attribute).order_by(Attribute.name)
    if applies_to:
        q = q.where(Attribute.applies_to == applies_to)
    result = await session.execute(q)
    return list(result.scalars().all())


@attr_router.post("", response_model=AttributeRead, status_code=status.HTTP_201_CREATED)
async def create_attribute(
    payload: AttributeCreate,
    _user: Annotated[User, Depends(require_permission("dictionaries.create"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Attribute:
    # Code uniqueness
    exists = await session.execute(select(Attribute).where(Attribute.code == payload.code))
    if exists.scalar_one_or_none():
        raise HTTPException(409, "Attribute with this code already exists")

    if payload.parent_id:
        parent = await session.get(Attribute, payload.parent_id)
        if parent is None:
            raise HTTPException(404, "Parent attribute not found")

    # Validate enum_values when data_type=enum
    if payload.data_type == "enum" and not payload.enum_values:
        raise HTTPException(422, "enum_values required for data_type=enum")

    attr = Attribute(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        data_type=payload.data_type,
        enum_values=payload.enum_values,
        default_value=payload.default_value,
        scope=payload.scope,
        applies_to=payload.applies_to,
        parent_id=payload.parent_id,
        is_group=payload.is_group,
        is_system=False,
    )
    session.add(attr)
    await session.commit()
    await session.refresh(attr)
    return attr


@attr_router.patch("/{attr_id}", response_model=AttributeRead)
async def update_attribute(
    attr_id: UUID,
    payload: AttributeUpdate,
    _user: Annotated[User, Depends(require_permission("dictionaries.edit"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Attribute:
    attr = await session.get(Attribute, attr_id)
    if attr is None:
        raise HTTPException(404, "Attribute not found")

    if payload.name is not None:
        attr.name = payload.name
    if payload.description is not None:
        attr.description = payload.description
    if payload.enum_values is not None:
        attr.enum_values = payload.enum_values
    if payload.default_value is not None:
        attr.default_value = payload.default_value
    if payload.parent_id is not None:
        if payload.parent_id == attr.id:
            raise HTTPException(400, "Cannot be own parent")
        cursor_id = payload.parent_id
        for _ in range(100):
            if cursor_id is None:
                break
            if cursor_id == attr.id:
                raise HTTPException(400, "Cycle detected")
            cursor = await session.get(Attribute, cursor_id)
            if cursor is None:
                break
            cursor_id = cursor.parent_id
        attr.parent_id = payload.parent_id

    await session.commit()
    await session.refresh(attr)
    return attr


@attr_router.delete("/{attr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribute(
    attr_id: UUID,
    _user: Annotated[User, Depends(require_permission("dictionaries.delete"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    attr = await session.get(Attribute, attr_id)
    if attr is None:
        raise HTTPException(404, "Attribute not found")
    if attr.is_system:
        raise HTTPException(400, "System attributes cannot be deleted")
    await session.delete(attr)
    await session.commit()


# ── Values ───────────────────────────────────────────────────────────────────

@val_router.get("", response_model=list[AttributeValueRead])
async def list_values(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    entity_type: str | None = None,
    entity_id: UUID | None = None,
) -> list[AttributeValue]:
    q = select(AttributeValue)
    if entity_type:
        q = q.where(AttributeValue.entity_type == entity_type)
    if entity_id:
        q = q.where(AttributeValue.entity_id == entity_id)
    result = await session.execute(q)
    return list(result.scalars().all())


@val_router.put("", response_model=AttributeValueRead)
async def upsert_value(
    payload: AttributeValueSet,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AttributeValue:
    """Set or update a value for (attribute, entity_type, entity_id).

    Permission: anyone authenticated. Granular per-attribute access
    control belongs to the attribute_values entity itself, not this
    layer.
    """
    attr = await session.get(Attribute, payload.attribute_id)
    if attr is None:
        raise HTTPException(404, "Attribute not found")

    # Validate value matches data_type
    val = payload.value
    if val is not None:
        ok = (
            (attr.data_type == "string" and isinstance(val, str))
            or (attr.data_type == "number" and isinstance(val, (int, float)))
            or (attr.data_type == "boolean" and isinstance(val, bool))
            or (attr.data_type == "enum" and isinstance(val, str)
                and val in (attr.enum_values or []))
        )
        if not ok:
            raise HTTPException(
                422,
                f"Value type mismatch: expected {attr.data_type} "
                f"({'one of ' + str(attr.enum_values) if attr.data_type == 'enum' else ''})",
            )

    # Upsert
    result = await session.execute(
        select(AttributeValue).where(
            AttributeValue.attribute_id == payload.attribute_id,
            AttributeValue.entity_type == payload.entity_type,
            AttributeValue.entity_id == payload.entity_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        existing = AttributeValue(
            attribute_id=payload.attribute_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            value=val,
        )
        session.add(existing)
    else:
        existing.value = val

    await session.commit()
    await session.refresh(existing)
    return existing


@val_router.delete("/{value_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_value(
    value_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    val = await session.get(AttributeValue, value_id)
    if val is None:
        raise HTTPException(404, "Value not found")
    await session.delete(val)
    await session.commit()
