"""/api/devices and /api/admin/devices — device configuration management.

Admin endpoints let administrators curate which device + OS version
combinations are available to testers in the "New Run" modal. The
worker reports all physically available runtimes on startup; the admin
picks which ones to expose.

The public ``GET /api/devices`` returns only ``is_active=True`` entries
for the tester's dropdown.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_admin
from app.config import settings
from app.db import get_async_session
from app.models.device_config import DeviceConfig
from app.redis_bus import get_redis
from app.models.user import User
from app.schemas.run import (
    DeviceConfigCreate,
    DeviceConfigRead,
    DeviceConfigUpdate,
    SimulatorConfigReport,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/admin/devices", tags=["devices"])
public_router = APIRouter(prefix="/api/devices", tags=["devices"])


# ────────────────────────── public (active devices for testers) ──

@public_router.get("", response_model=list[DeviceConfigRead])
async def list_active_devices(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[DeviceConfig]:
    """Return only is_active=True device configs (for New Run dropdown)."""
    result = await session.execute(
        select(DeviceConfig)
        .where(DeviceConfig.is_active.is_(True))
        .order_by(DeviceConfig.platform, DeviceConfig.device_type)
    )
    return list(result.scalars().all())


# ──────────────────────────── admin CRUD ──

@admin_router.get("", response_model=list[DeviceConfigRead])
async def list_all_devices(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[DeviceConfig]:
    result = await session.execute(
        select(DeviceConfig).order_by(DeviceConfig.platform, DeviceConfig.device_type)
    )
    return list(result.scalars().all())


@admin_router.post(
    "",
    response_model=DeviceConfigRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_device_config(
    payload: DeviceConfigCreate,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DeviceConfig:
    device = DeviceConfig(
        platform=payload.platform,
        device_type=payload.device_type,
        device_identifier=payload.device_identifier,
        os_version=payload.os_version,
        os_identifier=payload.os_identifier,
        is_active=True,
        created_by_user_id=admin.id,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return device


@admin_router.patch("/{device_id}", response_model=DeviceConfigRead)
async def update_device_config(
    device_id: UUID,
    payload: DeviceConfigUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DeviceConfig:
    result = await session.execute(
        select(DeviceConfig).where(DeviceConfig.id == device_id)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(404, "Device config not found")
    if payload.is_active is not None:
        device.is_active = payload.is_active
    await session.commit()
    await session.refresh(device)
    return device


@admin_router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device_config(
    device_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(DeviceConfig).where(DeviceConfig.id == device_id)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(404, "Device config not found")
    await session.delete(device)
    await session.commit()


# ─────────────── worker-reported available configs (Redis cache) ──

@admin_router.get("/available", response_model=SimulatorConfigReport)
async def get_available_configs(
    _admin: Annotated[User, Depends(require_admin)],
) -> SimulatorConfigReport:
    """Return all runtimes + device types the worker reported as available."""
    redis = get_redis()
    raw = await redis.get("simulator:config")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No worker has reported simulator capabilities yet. "
            "Make sure the host worker is running (make start).",
        )
    return SimulatorConfigReport.model_validate_json(raw)
