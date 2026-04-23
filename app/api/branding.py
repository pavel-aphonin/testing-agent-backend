"""System branding API — product name + logo customization.

  GET  /api/branding                    public (no auth) — needed by login
  PATCH /api/branding                   admin-only — update names
  POST /api/branding/logo               admin-only — upload main logo
  POST /api/branding/logo-back          admin-only — upload flip-back
  DELETE /api/branding/logo             admin-only — drop main logo
  DELETE /api/branding/logo-back        admin-only — drop flip-back
  DELETE /api/branding                  admin-only — reset everything

Logos live under ``{app_uploads_dir}/branding/`` and are served by the
existing ``/app-bundles``-sibling static mount at ``/branding-assets``
(declared in main.py). We store the file with a short random suffix so
subsequent uploads don't get cached by the browser under the old URL.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.config import settings
from app.db import get_async_session
from app.models.branding import BRANDING_SINGLETON_ID, SystemBranding
from app.models.user import User

router = APIRouter(prefix="/api/branding", tags=["branding"])

# Where logo files live on disk (path is relative so we can cross the
# Docker boundary cleanly).
BRANDING_SUBDIR = "branding"

# Guard on uploads. The component renders at 32–64 CSS px, so we don't
# need a 20 MB PNG — 2 MB is plenty for a high-res logo.
MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
# Favicon has a slightly different whitelist — ``.ico`` is the classic
# format, and we explicitly allow it even though browsers happily
# render PNG/SVG favicons too.
FAVICON_EXTENSIONS = {".ico", ".png", ".svg", ".webp"}
MAX_FAVICON_BYTES = 512 * 1024  # 512 KB is absurdly generous for 32×32


# ── Schemas ──────────────────────────────────────────────────────────────────


class BrandingRead(BaseModel):
    """Values currently in the DB. Any null field is defaulted on the
    frontend to the built-in Markov branding."""

    model_config = ConfigDict(from_attributes=True)

    product_name: str | None
    short_name: str | None
    logo_path: str | None
    logo_back_path: str | None
    favicon_path: str | None
    # Full Ant Design token blob. See ``SystemBranding.theme_tokens`` for
    # shape. Null means "no overrides — use Markov defaults".
    theme_tokens: dict | None
    updated_at: datetime


class BrandingUpdate(BaseModel):
    # ``None`` is the reset signal for a given field (revert to default
    # Markov value). An empty string is treated the same — no reason for
    # a field to explicitly be empty once set.
    product_name: str | None = Field(default=None, max_length=80)
    short_name: str | None = Field(default=None, max_length=40)
    # Full token blob. Passing null wipes all overrides; passing a dict
    # replaces the whole blob (partial updates go through ``/theme``).
    theme_tokens: dict | None = Field(default=None)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_or_create(session: AsyncSession) -> SystemBranding:
    """Return the singleton row, creating it if the seed migration
    somehow skipped it (defensive)."""
    obj = await session.get(SystemBranding, BRANDING_SINGLETON_ID)
    if obj is None:
        obj = SystemBranding(id=BRANDING_SINGLETON_ID)
        session.add(obj)
        await session.flush()
    return obj


def _branding_dir() -> Path:
    p = Path(settings.app_uploads_dir) / BRANDING_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_upload(
    file: UploadFile,
    suffix_tag: str,
    *,
    allowed_ext: set[str] = ALLOWED_EXTENSIONS,
    max_bytes: int = MAX_LOGO_BYTES,
    type_error_message: str = "Поддерживаются PNG, JPG, WebP, SVG и GIF",
) -> str:
    """Persist the uploaded file and return the relative path to it.

    We include a random suffix in the filename so the new file can't
    collide with a cached copy of the old one at the same URL.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(400, type_error_message)
    blob = file.file.read()
    if len(blob) > max_bytes:
        kb = max_bytes // 1024
        mb = max_bytes // 1024 // 1024
        # "2 МБ" reads better than "2048 КБ"; show whichever is cleaner.
        limit = f"{mb} МБ" if max_bytes >= 1024 * 1024 else f"{kb} КБ"
        raise HTTPException(400, f"Файл больше {limit}")

    tag = secrets.token_hex(4)
    fname = f"{suffix_tag}-{tag}{ext}"
    (_branding_dir() / fname).write_bytes(blob)
    # Path is stored relative to app_uploads_dir — /branding-assets static
    # mount serves everything under that root.
    return f"{BRANDING_SUBDIR}/{fname}"


def _unlink(relpath: str | None) -> None:
    if not relpath:
        return
    try:
        (Path(settings.app_uploads_dir) / relpath).unlink()
    except OSError:
        pass


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("", response_model=BrandingRead)
async def get_branding(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    """Open to anyone (no auth). Needed for the login page and the
    browser tab title, both of which render before a JWT exists.
    Contains no secrets — just UI decoration."""
    res = await session.execute(select(SystemBranding))
    obj = res.scalar_one_or_none()
    if obj is None:
        # Shouldn't happen after the seed migration, but be defensive.
        obj = await _get_or_create(session)
        await session.commit()
    return BrandingRead.model_validate(obj)


@router.patch("", response_model=BrandingRead)
async def update_branding(
    payload: BrandingUpdate,
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    obj = await _get_or_create(session)
    # We use Pydantic's ``model_fields_set`` to tell "client sent this
    # field, maybe as null" apart from "client did not mention it at
    # all". Null means clear; the absence of the key means don't touch.
    sent = payload.model_fields_set
    if "product_name" in sent:
        obj.product_name = (payload.product_name or "").strip() or None
    if "short_name" in sent:
        obj.short_name = (payload.short_name or "").strip() or None
    if "theme_tokens" in sent:
        # Empty dict → drop to null so the frontend falls back to the
        # built-in Markov palette without having to distinguish "all
        # defaults" from "not configured".
        obj.theme_tokens = payload.theme_tokens or None
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    return BrandingRead.model_validate(obj)


@router.post("/logo", response_model=BrandingRead)
async def upload_logo(
    file: UploadFile,
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    obj = await _get_or_create(session)
    new_path = _save_upload(file, "logo")
    old = obj.logo_path
    obj.logo_path = new_path
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    return BrandingRead.model_validate(obj)


@router.post("/logo-back", response_model=BrandingRead)
async def upload_logo_back(
    file: UploadFile,
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    """Back face of the flip animation. Only rendered when both
    ``logo_path`` and ``logo_back_path`` are set."""
    obj = await _get_or_create(session)
    new_path = _save_upload(file, "logo-back")
    old = obj.logo_back_path
    obj.logo_back_path = new_path
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    return BrandingRead.model_validate(obj)


@router.delete("/logo", response_model=BrandingRead)
async def delete_logo(
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    obj = await _get_or_create(session)
    old = obj.logo_path
    obj.logo_path = None
    # If the back image is set without a main, the flip has nothing to
    # flip from. Drop it too so the UI doesn't end up in a weird state.
    old_back = obj.logo_back_path
    obj.logo_back_path = None
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    _unlink(old_back)
    return BrandingRead.model_validate(obj)


@router.delete("/logo-back", response_model=BrandingRead)
async def delete_logo_back(
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    obj = await _get_or_create(session)
    old = obj.logo_back_path
    obj.logo_back_path = None
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    return BrandingRead.model_validate(obj)


# ── Favicon ──────────────────────────────────────────────────────────────────


@router.post("/favicon", response_model=BrandingRead)
async def upload_favicon(
    file: UploadFile,
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    """Browser-tab icon. Prefer PNG 32×32 or SVG — .ico also accepted."""
    obj = await _get_or_create(session)
    new_path = _save_upload(
        file,
        "favicon",
        allowed_ext=FAVICON_EXTENSIONS,
        max_bytes=MAX_FAVICON_BYTES,
        type_error_message="Поддерживаются ICO, PNG, SVG и WebP",
    )
    old = obj.favicon_path
    obj.favicon_path = new_path
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    return BrandingRead.model_validate(obj)


@router.delete("/favicon", response_model=BrandingRead)
async def delete_favicon(
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    obj = await _get_or_create(session)
    old = obj.favicon_path
    obj.favicon_path = None
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old)
    return BrandingRead.model_validate(obj)


@router.delete("", response_model=BrandingRead)
async def reset_branding(
    admin: Annotated[User, Depends(require_permission("users.view"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> BrandingRead:
    """Drop all overrides — the UI goes back to animated Markov branding."""
    obj = await _get_or_create(session)
    old_logo = obj.logo_path
    old_back = obj.logo_back_path
    old_fav = obj.favicon_path
    obj.product_name = None
    obj.short_name = None
    obj.logo_path = None
    obj.logo_back_path = None
    obj.favicon_path = None
    obj.theme_tokens = None
    obj.updated_by_user_id = admin.id
    await session.commit()
    await session.refresh(obj)
    _unlink(old_logo)
    _unlink(old_back)
    _unlink(old_fav)
    return BrandingRead.model_validate(obj)
