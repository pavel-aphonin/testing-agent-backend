"""Reference dictionary endpoints + user table preferences.

GET endpoints are open to any authenticated user (frontend pickers
need them). POST/PATCH/DELETE require ``dictionaries.*`` permissions.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.db import get_async_session
from app.models.reference import (
    RefActionType,
    RefAppCategory,
    RefDeviceType,
    RefOsVersion,
    RefPlatform,
    RefTestDataType,
    WorkspaceActionSetting,
)
from app.models.user import User
from app.models.user_table_pref import UserTablePref

router = APIRouter(prefix="/api/reference", tags=["reference"])
prefs_router = APIRouter(prefix="/api/me/table-prefs", tags=["table-prefs"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class _RefBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    is_active: bool = True


class PlatformRead(_RefBase):
    sort_order: int = 0


class OsVersionRead(_RefBase):
    platform_code: str


class DeviceTypeRead(_RefBase):
    platform_code: str


class ActionTypeRead(_RefBase):
    description: str | None = None
    platform_scope: str = "universal"
    is_system: bool = True


class TestDataTypeRead(_RefBase):
    is_system: bool = True


class AppCategoryRead(_RefBase):
    icon: str | None = None
    sort_order: int = 0
    is_system: bool = False


class _CreateBase(BaseModel):
    code: str
    name: str
    is_active: bool = True


class PlatformCreate(_CreateBase):
    sort_order: int = 0


class OsVersionCreate(_CreateBase):
    platform_code: str


class DeviceTypeCreate(_CreateBase):
    platform_code: str


class ActionTypeCreate(_CreateBase):
    description: str | None = None
    platform_scope: str = "universal"


class TestDataTypeCreate(_CreateBase):
    pass


class AppCategoryCreate(_CreateBase):
    icon: str | None = None
    sort_order: int = 0


# ── List endpoints (open to any user) ────────────────────────────────────────

@router.get("/platforms", response_model=list[PlatformRead])
async def list_platforms(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    r = await session.execute(select(RefPlatform).order_by(RefPlatform.sort_order, RefPlatform.name))
    return list(r.scalars().all())


@router.get("/os-versions", response_model=list[OsVersionRead])
async def list_os_versions(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    platform: str | None = None,
):
    q = select(RefOsVersion)
    if platform:
        q = q.where(RefOsVersion.platform_code == platform)
    q = q.order_by(RefOsVersion.name)
    r = await session.execute(q)
    return list(r.scalars().all())


@router.get("/device-types", response_model=list[DeviceTypeRead])
async def list_device_types(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    platform: str | None = None,
):
    q = select(RefDeviceType)
    if platform:
        q = q.where(RefDeviceType.platform_code == platform)
    q = q.order_by(RefDeviceType.name)
    r = await session.execute(q)
    return list(r.scalars().all())


@router.get("/action-types", response_model=list[ActionTypeRead])
async def list_action_types(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    platform: str | None = None,
):
    q = select(RefActionType)
    if platform:
        q = q.where(
            (RefActionType.platform_scope == platform)
            | (RefActionType.platform_scope == "universal")
        )
    q = q.order_by(RefActionType.name)
    r = await session.execute(q)
    return list(r.scalars().all())


@router.get("/test-data-types", response_model=list[TestDataTypeRead])
async def list_test_data_types(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    r = await session.execute(select(RefTestDataType).order_by(RefTestDataType.name))
    return list(r.scalars().all())


@router.get("/app-categories", response_model=list[AppCategoryRead])
async def list_app_categories(
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    active_only: bool = False,
):
    q = select(RefAppCategory)
    if active_only:
        q = q.where(RefAppCategory.is_active.is_(True))
    q = q.order_by(RefAppCategory.sort_order, RefAppCategory.name)
    r = await session.execute(q)
    return list(r.scalars().all())


# ── Generic mutation handler factory ─────────────────────────────────────────
# Admin CRUD for each ref table is largely identical. Factories below
# build the routes to avoid 200+ lines of copy-paste.

def _make_admin_routes(model, prefix: str, create_schema, read_schema):
    # With ``from __future__ import annotations`` all type hints become
    # strings at function-definition time. FastAPI's OpenAPI generator
    # tries to resolve ``"create_schema"`` and fails because that's a
    # local variable of this factory, not a module-level symbol — which
    # used to silently work until /openapi.json got exercised by the
    # new Settings → API tab. Fix: rewrite the annotation with the real
    # class object immediately after defining the function, so FastAPI
    # reads the actual Pydantic model via ``get_type_hints`` / direct
    # ``__annotations__`` access.
    async def _create(
        payload,  # annotation patched below
        _u: Annotated[User, Depends(require_permission("dictionaries.create"))],
        session: Annotated[AsyncSession, Depends(get_async_session)],
    ):
        exists = await session.execute(select(model).where(model.code == payload.code))
        if exists.scalar_one_or_none():
            raise HTTPException(409, "Code already exists")
        obj = model(**payload.model_dump())
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return obj

    _create.__annotations__["payload"] = create_schema
    router.post(f"/{prefix}", response_model=read_schema, status_code=201)(_create)

    @router.patch(f"/{prefix}/{{item_id}}", response_model=read_schema)
    async def _update(
        item_id: UUID,
        payload: dict,
        _u: Annotated[User, Depends(require_permission("dictionaries.edit"))],
        session: Annotated[AsyncSession, Depends(get_async_session)],
    ):
        obj = await session.get(model, item_id)
        if obj is None:
            raise HTTPException(404, "Not found")
        for k, v in payload.items():
            if hasattr(obj, k) and k != "id":
                setattr(obj, k, v)
        await session.commit()
        await session.refresh(obj)
        return obj

    @router.delete(f"/{prefix}/{{item_id}}", status_code=204)
    async def _delete(
        item_id: UUID,
        _u: Annotated[User, Depends(require_permission("dictionaries.delete"))],
        session: Annotated[AsyncSession, Depends(get_async_session)],
    ):
        obj = await session.get(model, item_id)
        if obj is None:
            raise HTTPException(404, "Not found")

        # App categories have special delete semantics: we allow removing
        # even the system-seeded rows if nothing uses them. The referenced
        # check queries AppPackage.category against this row's code — any
        # hit means some package in the catalog points at this category
        # and we'd leave it orphaned.
        if model is RefAppCategory:
            from app.models.app_package import AppPackage
            q = await session.execute(
                select(AppPackage.id, AppPackage.name)
                .where(AppPackage.category == obj.code)
                .limit(5)
            )
            using = q.all()
            if using:
                names = ", ".join(f"«{n}»" for _, n in using[:3])
                more = "" if len(using) < 4 else f" и ещё {len(using) - 3}"
                raise HTTPException(
                    409,
                    (
                        f"Нельзя удалить категорию — её используют приложения: "
                        f"{names}{more}. Сначала переведите их в другую "
                        f"категорию или удалите сами приложения."
                    ),
                )
            # No references → category can go even if it was a system seed.
            await session.delete(obj)
            await session.commit()
            return

        if hasattr(obj, "is_system") and obj.is_system:
            raise HTTPException(400, "Системные записи нельзя удалить")
        await session.delete(obj)
        await session.commit()


_make_admin_routes(RefPlatform, "platforms", PlatformCreate, PlatformRead)
_make_admin_routes(RefOsVersion, "os-versions", OsVersionCreate, OsVersionRead)
_make_admin_routes(RefDeviceType, "device-types", DeviceTypeCreate, DeviceTypeRead)
_make_admin_routes(RefActionType, "action-types", ActionTypeCreate, ActionTypeRead)
_make_admin_routes(RefTestDataType, "test-data-types", TestDataTypeCreate, TestDataTypeRead)
_make_admin_routes(RefAppCategory, "app-categories", AppCategoryCreate, AppCategoryRead)


# ── Workspace action settings ────────────────────────────────────────────────

@router.get("/workspaces/{ws_id}/action-settings")
async def list_ws_action_settings(
    ws_id: UUID,
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    r = await session.execute(
        select(WorkspaceActionSetting).where(WorkspaceActionSetting.workspace_id == ws_id)
    )
    rows = r.scalars().all()
    return [
        {
            "id": str(s.id),
            "workspace_id": str(s.workspace_id),
            "action_type_id": str(s.action_type_id),
            "is_enabled": s.is_enabled,
        }
        for s in rows
    ]


@router.put("/workspaces/{ws_id}/action-settings")
async def upsert_ws_action_setting(
    ws_id: UUID,
    payload: dict,
    _u: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    type_id = UUID(payload["action_type_id"])
    enabled = bool(payload.get("is_enabled", True))
    r = await session.execute(
        select(WorkspaceActionSetting).where(
            WorkspaceActionSetting.workspace_id == ws_id,
            WorkspaceActionSetting.action_type_id == type_id,
        )
    )
    existing = r.scalar_one_or_none()
    if existing is None:
        existing = WorkspaceActionSetting(
            workspace_id=ws_id, action_type_id=type_id, is_enabled=enabled,
        )
        session.add(existing)
    else:
        existing.is_enabled = enabled
    await session.commit()
    return {"action_type_id": str(type_id), "is_enabled": enabled}


# ── User table preferences ───────────────────────────────────────────────────

@prefs_router.get("/{table_key}")
async def get_prefs(
    table_key: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    r = await session.execute(
        select(UserTablePref).where(
            UserTablePref.user_id == user.id,
            UserTablePref.table_key == table_key,
        )
    )
    row = r.scalar_one_or_none()
    return row.prefs if row else {}


@prefs_router.put("/{table_key}")
async def set_prefs(
    table_key: str,
    payload: dict,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    r = await session.execute(
        select(UserTablePref).where(
            UserTablePref.user_id == user.id,
            UserTablePref.table_key == table_key,
        )
    )
    row = r.scalar_one_or_none()
    if row is None:
        row = UserTablePref(user_id=user.id, table_key=table_key, prefs=payload)
        session.add(row)
    else:
        row.prefs = payload
    await session.commit()
    return payload
