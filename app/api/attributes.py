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


async def _validate_value(attr: Attribute, val: object, session: AsyncSession) -> bool:
    """Type-check a value against an attribute's data_type.

    Returns True if value is acceptable. For ``member``, also verifies the
    user exists in the database.
    """
    import re
    from datetime import datetime as _dt
    from uuid import UUID as _UUID

    dt = attr.data_type
    if dt == "string":
        return isinstance(val, str)
    if dt == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if dt == "boolean":
        return isinstance(val, bool)
    if dt == "enum":
        # Source-dictionary mode: value must be a UUID of an item in the
        # referenced dictionary.
        if attr.source_dictionary_id is not None:
            from app.models.custom_dictionary import CustomDictionaryItem
            if not isinstance(val, str):
                return False
            try:
                item_uuid = _UUID(val)
            except (ValueError, TypeError):
                return False
            res = await session.execute(
                select(CustomDictionaryItem).where(
                    CustomDictionaryItem.id == item_uuid,
                    CustomDictionaryItem.dictionary_id == attr.source_dictionary_id,
                )
            )
            return res.scalar_one_or_none() is not None
        # Static enum_values mode
        return isinstance(val, str) and val in (attr.enum_values or [])
    if dt == "date":
        if not isinstance(val, str):
            return False
        try:
            _dt.fromisoformat(val.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    if dt == "link":
        if not isinstance(val, str):
            return False
        return bool(re.match(r"^https?://[^\s]+$", val))
    if dt == "member":
        if not isinstance(val, str):
            return False
        try:
            user_uuid = _UUID(val)
        except (ValueError, TypeError):
            return False
        # Verify user exists
        u = await session.get(User, user_uuid)
        return u is not None
    return False


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
        is_required=payload.is_required,
        source_dictionary_id=payload.source_dictionary_id,
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
    if payload.is_required is not None:
        attr.is_required = payload.is_required
    if payload.source_dictionary_id is not None:
        attr.source_dictionary_id = payload.source_dictionary_id
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

    # Required check
    val = payload.value
    if attr.is_required and (val is None or val == "" or val == []):
        raise HTTPException(
            422,
            f"Атрибут «{attr.name}» обязателен для заполнения",
        )

    # Validate value matches data_type
    if val is not None:
        ok = await _validate_value(attr, val, session)
        if not ok:
            hint = ""
            if attr.data_type == "enum":
                hint = f" (одно из: {attr.enum_values})"
            elif attr.data_type == "date":
                hint = " (ISO-8601 строка: YYYY-MM-DD или с временем)"
            elif attr.data_type == "link":
                hint = " (URL начинающийся с http:// или https://)"
            elif attr.data_type == "member":
                hint = " (UUID существующего пользователя)"
            raise HTTPException(
                422,
                f"Несоответствие типа: ожидается {attr.data_type}{hint}",
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
