"""/api/apps — the extension store + installation + reviews.

Surface:
  GET    /api/apps/store                 search the public catalog
  GET    /api/apps/{pkg_id}              package details (with versions)
  GET    /api/apps/{pkg_id}/versions     list versions
  GET    /api/apps/{pkg_id}/reviews      reviews
  POST   /api/apps/{pkg_id}/reviews      add/update your review
  DELETE /api/apps/{pkg_id}/reviews      remove your review
  POST   /api/apps/upload                upload a ZIP bundle
  POST   /api/apps/{pkg_id}/submit       submit a DRAFT for admin review
  POST   /api/apps/{pkg_id}/approve      admin: approve or reject
  GET    /api/apps/mine                  packages I uploaded
  GET    /api/apps/admin/all             admin: everything
  GET    /api/apps/admin/pending         admin: queue of pending reviews
  GET    /api/apps/bundles/{pkg_id}/{version}/{*file}
                                         serve static files from a bundle
  GET    /api/workspaces/{ws}/apps       installed in a workspace
  POST   /api/workspaces/{ws}/apps       install
  PATCH  /api/workspaces/{ws}/apps/{inst_id}
                                         change version / settings / enabled
  DELETE /api/workspaces/{ws}/apps/{inst_id}
                                         uninstall
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.config import settings
from app.db import get_async_session
from app.models.app_audit import AppInstallationAudit, AppInstallationAuditAction
from app.models.app_package import (
    AppApprovalStatus,
    AppInstallation,
    AppInstallationUserPref,
    AppPackage,
    AppPackageVersion,
    AppReview,
)
from app.models.user import User
from app.models.workspace import WorkspaceMember, WsRole
from app.schemas.app_package import (
    AppApprovalDecision,
    AppInstallationRead,
    AppInstallationUserPrefsUpdate,
    AppInstallRequest,
    AppInstallUpdate,
    AppPackageRead,
    AppPackageVersionRead,
    AppPublishRequest,
    AppReviewRead,
    AppReviewUpsert,
)
from app.services.app_bundle import BundleError, extract_and_validate
from app.services.app_token import issue_installation_token, require_installation_token

router = APIRouter(prefix="/api/apps", tags=["apps"])
ws_apps_router = APIRouter(prefix="/api/workspaces", tags=["workspace-apps"])


def _has_perm(user: User, perm: str) -> bool:
    return perm in (user.permissions or [])


def _audit(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    installation_id: UUID | None,
    app_package_id: UUID | None,
    package_name: str | None,
    action: AppInstallationAuditAction,
    user: User,
    from_version: str | None = None,
    to_version: str | None = None,
    details: dict | None = None,
) -> None:
    """Record an audit row. Does not commit — the caller's commit ships it.

    Keeping this inline rather than in a service module because it's a
    3-line side-effect and wrapping it would hide the most important
    callsites (install / update / uninstall).
    """
    session.add(
        AppInstallationAudit(
            workspace_id=workspace_id,
            installation_id=installation_id,
            app_package_id=app_package_id,
            package_name=package_name,
            action=action.value,
            from_version=from_version,
            to_version=to_version,
            details=details,
            user_id=user.id,
            user_email=user.email,
        )
    )


async def _enrich_package(pkg: AppPackage, session: AsyncSession) -> dict:
    """Return AppPackageRead-compatible dict with aggregate fields filled."""
    # Latest non-deprecated version
    vq = await session.execute(
        select(AppPackageVersion)
        .where(
            AppPackageVersion.app_package_id == pkg.id,
            AppPackageVersion.is_deprecated.is_(False),
        )
        .order_by(AppPackageVersion.created_at.desc())
        .limit(1)
    )
    latest_v = vq.scalar_one_or_none()

    inst_q = await session.execute(
        select(func.count()).select_from(AppInstallation).where(
            AppInstallation.app_package_id == pkg.id
        )
    )
    install_count = inst_q.scalar() or 0

    rev_q = await session.execute(
        select(func.avg(AppReview.rating), func.count()).where(
            AppReview.app_package_id == pkg.id
        )
    )
    avg, cnt = rev_q.first() or (None, 0)

    return {
        "id": pkg.id,
        "code": pkg.code,
        "name": pkg.name,
        "description": pkg.description,
        "category": pkg.category,
        "author": pkg.author,
        "logo_path": pkg.logo_path,
        "cover_path": pkg.cover_path,
        "is_public": pkg.is_public,
        "owner_workspace_id": pkg.owner_workspace_id,
        "approval_status": pkg.approval_status,
        "approved_by_user_id": pkg.approved_by_user_id,
        "approved_at": pkg.approved_at,
        "rejection_reason": pkg.rejection_reason,
        "created_by_user_id": pkg.created_by_user_id,
        "created_at": pkg.created_at,
        "latest_version": latest_v.version if latest_v else None,
        "install_count": int(install_count),
        "avg_rating": float(avg) if avg is not None else None,
        "review_count": int(cnt or 0),
    }


# ── Store search ─────────────────────────────────────────────────────────────

@router.get("/store", response_model=list[AppPackageRead])
async def search_store(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    q: str | None = Query(default=None, description="Поиск по названию / коду / автору"),
    category: str | None = None,
) -> list[dict]:
    """Search the public catalog + all private apps of workspaces I'm in."""
    # Get my workspaces (for private apps visibility)
    mem_q = await session.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    my_ws_ids = [m[0] for m in mem_q.all()]

    stmt = select(AppPackage).where(
        AppPackage.approval_status == AppApprovalStatus.APPROVED.value,
    )
    # Public OR private in a workspace I belong to
    if my_ws_ids:
        stmt = stmt.where(
            (AppPackage.is_public.is_(True))
            | (AppPackage.owner_workspace_id.in_(my_ws_ids))
        )
    else:
        stmt = stmt.where(AppPackage.is_public.is_(True))

    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(AppPackage.name).like(like)
            | func.lower(AppPackage.code).like(like)
            | func.lower(func.coalesce(AppPackage.author, "")).like(like)
        )
    if category:
        stmt = stmt.where(AppPackage.category == category)
    stmt = stmt.order_by(AppPackage.name)

    res = await session.execute(stmt)
    pkgs = res.scalars().all()
    return [await _enrich_package(p, session) for p in pkgs]


# ── Package details ──────────────────────────────────────────────────────────

@router.get("/mine", response_model=list[AppPackageRead])
async def my_packages(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    """Packages uploaded by the current user (for Profile → My Apps)."""
    res = await session.execute(
        select(AppPackage)
        .where(AppPackage.created_by_user_id == user.id)
        .order_by(AppPackage.created_at.desc())
    )
    return [await _enrich_package(p, session) for p in res.scalars().all()]


@router.get("/admin/all", response_model=list[AppPackageRead])
async def admin_all_packages(
    _user: Annotated[User, Depends(require_permission("apps.moderate"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    res = await session.execute(select(AppPackage).order_by(AppPackage.created_at.desc()))
    return [await _enrich_package(p, session) for p in res.scalars().all()]


@router.get("/admin/pending", response_model=list[AppPackageRead])
async def admin_pending(
    _user: Annotated[User, Depends(require_permission("apps.moderate"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    res = await session.execute(
        select(AppPackage)
        .where(AppPackage.approval_status == AppApprovalStatus.PENDING.value)
        .order_by(AppPackage.created_at.asc())
    )
    return [await _enrich_package(p, session) for p in res.scalars().all()]


@router.get("/{pkg_id}", response_model=AppPackageRead)
async def get_package(
    pkg_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None:
        raise HTTPException(404, "Package not found")
    return await _enrich_package(pkg, session)


@router.get("/{pkg_id}/versions", response_model=list[AppPackageVersionRead])
async def list_versions(
    pkg_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[AppPackageVersion]:
    res = await session.execute(
        select(AppPackageVersion)
        .where(AppPackageVersion.app_package_id == pkg_id)
        .order_by(AppPackageVersion.created_at.desc())
    )
    return list(res.scalars().all())


# ── Upload ───────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=AppPackageRead, status_code=201)
async def upload_bundle(
    file: UploadFile,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    is_public: bool = False,
    owner_workspace_id: UUID | None = None,
    submit_for_review: bool = True,
) -> dict:
    """Upload a ZIP bundle.

    Rules:
      - Requires the ``apps.upload`` permission. Admins inherit it via
        the system admin role; other roles must be granted it explicitly.
      - Regular uploaders: can upload, goes to DRAFT or PENDING depending
        on ``submit_for_review``.
      - Admins (users.view): can upload AND immediately auto-approve by
        passing ``submit_for_review=false``.
      - Private apps require ``owner_workspace_id`` pointing to a workspace
        the caller is a member of.
    """
    if "apps.upload" not in (user.permissions or []):
        raise HTTPException(
            403,
            "У вас нет прав на загрузку приложений в магазин (требуется apps.upload)",
        )
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Ожидается ZIP-архив")

    content = await file.read()
    try:
        extracted = extract_and_validate(content)
    except BundleError as e:
        raise HTTPException(400, str(e)) from e

    manifest = extracted.manifest
    is_admin = _has_perm(user, "users.view")

    # Validate private-ownership rules
    if not is_public and owner_workspace_id is None and not is_admin:
        # Default private apps to the uploader's first workspace — OR refuse
        mem_q = await session.execute(
            select(WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
            .limit(1)
        )
        first = mem_q.scalar_one_or_none()
        if first is None:
            raise HTTPException(400, "Укажите рабочее пространство-владельца")
        owner_workspace_id = first
    if owner_workspace_id is not None:
        mem_q = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == owner_workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if mem_q.scalar_one_or_none() is None and not is_admin:
            raise HTTPException(403, "Вы не состоите в этом рабочем пространстве")

    # Find or create package
    pq = await session.execute(select(AppPackage).where(AppPackage.code == manifest.code))
    pkg = pq.scalar_one_or_none()
    if pkg is None:
        pkg = AppPackage(
            code=manifest.code,
            name=manifest.name,
            description=manifest.description,
            category=manifest.category,
            author=manifest.author,
            logo_path=extracted.logo_relpath,
            cover_path=extracted.cover_relpath,
            is_public=is_public,
            owner_workspace_id=owner_workspace_id,
            created_by_user_id=user.id,
            approval_status=AppApprovalStatus.DRAFT.value,
        )
        session.add(pkg)
        await session.flush()
    else:
        # Only the original uploader or an admin may push new versions.
        if pkg.created_by_user_id != user.id and not is_admin:
            raise HTTPException(
                403,
                "Приложение с таким кодом уже существует и принадлежит другому пользователю",
            )
        pkg.name = manifest.name
        pkg.description = manifest.description
        pkg.category = manifest.category
        pkg.author = manifest.author
        if extracted.logo_relpath:
            pkg.logo_path = extracted.logo_relpath
        if extracted.cover_relpath:
            pkg.cover_path = extracted.cover_relpath

    # Reject duplicate version
    dup = await session.execute(
        select(AppPackageVersion).where(
            AppPackageVersion.app_package_id == pkg.id,
            AppPackageVersion.version == manifest.version,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(
            409, f"Версия {manifest.version} уже загружена для этого приложения",
        )

    # Register version
    version = AppPackageVersion(
        app_package_id=pkg.id,
        version=manifest.version,
        manifest=manifest.model_dump(),
        bundle_path=extracted.bundle_relpath,
        size_bytes=extracted.size_bytes,
        changelog=manifest.changelog,
    )
    session.add(version)

    # Update approval state
    if is_admin and not submit_for_review:
        # Admin uploads auto-approve
        pkg.approval_status = AppApprovalStatus.APPROVED.value
        pkg.approved_by_user_id = user.id
        pkg.approved_at = datetime.now(timezone.utc)
    elif submit_for_review:
        pkg.approval_status = AppApprovalStatus.PENDING.value
    # else: stays DRAFT (if new) or retains current status

    await session.commit()
    await session.refresh(pkg)
    return await _enrich_package(pkg, session)


# ── Submit for review / approve ──────────────────────────────────────────────

@router.post("/{pkg_id}/submit", response_model=AppPackageRead)
async def submit_for_review(
    pkg_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None:
        raise HTTPException(404, "Package not found")
    if pkg.created_by_user_id != user.id and not _has_perm(user, "users.view"):
        raise HTTPException(403, "Только владелец приложения может отправить его на модерацию")
    if pkg.approval_status not in (AppApprovalStatus.DRAFT.value, AppApprovalStatus.REJECTED.value):
        raise HTTPException(400, f"Нельзя отправить на модерацию в статусе {pkg.approval_status}")
    pkg.approval_status = AppApprovalStatus.PENDING.value
    pkg.rejection_reason = None
    await session.commit()
    return await _enrich_package(pkg, session)


@router.post("/{pkg_id}/approve", response_model=AppPackageRead)
async def approve_or_reject(
    pkg_id: UUID,
    decision: AppApprovalDecision,
    # Moderation is its own gate: you might have apps.upload but not the
    # authority to approve your own submissions. Admin has both.
    admin: Annotated[User, Depends(require_permission("apps.moderate"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None:
        raise HTTPException(404, "Package not found")
    if decision.approved:
        pkg.approval_status = AppApprovalStatus.APPROVED.value
        pkg.approved_by_user_id = admin.id
        pkg.approved_at = datetime.now(timezone.utc)
        pkg.rejection_reason = None
    else:
        pkg.approval_status = AppApprovalStatus.REJECTED.value
        pkg.rejection_reason = decision.rejection_reason
    await session.commit()
    return await _enrich_package(pkg, session)


@router.delete("/{pkg_id}", status_code=204)
async def delete_package(
    pkg_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None:
        raise HTTPException(404, "Package not found")
    is_admin = _has_perm(user, "users.view")
    if pkg.created_by_user_id != user.id and not is_admin:
        raise HTTPException(403, "Нет прав на удаление")
    await session.delete(pkg)
    await session.commit()


# ── Bundle static file serving ───────────────────────────────────────────────

@router.get("/bundles/{pkg_id}/{version}/{file_path:path}")
async def serve_bundle_file(
    pkg_id: UUID,
    version: str,
    file_path: str,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Serve static files from an app bundle. No extra ACL — if you're
    authenticated and the app is approved or private-to-your-ws, you can
    read its frontend."""
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None or pkg.approval_status != AppApprovalStatus.APPROVED.value:
        raise HTTPException(404, "Bundle not available")
    q = await session.execute(
        select(AppPackageVersion).where(
            AppPackageVersion.app_package_id == pkg_id,
            AppPackageVersion.version == version,
        )
    )
    ver = q.scalar_one_or_none()
    if ver is None:
        raise HTTPException(404, "Version not found")
    root = Path(settings.app_uploads_dir) / ver.bundle_path
    full = (root / file_path).resolve()
    # Reject path traversal
    if not str(full).startswith(str(root.resolve())):
        raise HTTPException(403, "Path escapes bundle")
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(full)


# ── Reviews ──────────────────────────────────────────────────────────────────

@router.get("/{pkg_id}/reviews", response_model=list[AppReviewRead])
async def list_reviews(
    pkg_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    res = await session.execute(
        select(AppReview)
        .where(AppReview.app_package_id == pkg_id)
        .order_by(AppReview.created_at.desc())
    )
    out = []
    for r in res.scalars().all():
        u = await session.get(User, r.user_id)
        out.append({
            "id": r.id,
            "app_package_id": r.app_package_id,
            "user_id": r.user_id,
            "user_email": u.email if u else "",
            "rating": r.rating,
            "text": r.text,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        })
    return out


@router.post("/{pkg_id}/reviews", response_model=AppReviewRead)
async def upsert_review(
    pkg_id: UUID,
    payload: AppReviewUpsert,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    pkg = await session.get(AppPackage, pkg_id)
    if pkg is None:
        raise HTTPException(404, "Package not found")

    res = await session.execute(
        select(AppReview).where(
            AppReview.app_package_id == pkg_id,
            AppReview.user_id == user.id,
        )
    )
    existing = res.scalar_one_or_none()
    if existing is None:
        existing = AppReview(
            app_package_id=pkg_id,
            user_id=user.id,
            rating=payload.rating,
            text=payload.text,
        )
        session.add(existing)
    else:
        existing.rating = payload.rating
        existing.text = payload.text

    await session.commit()
    await session.refresh(existing)
    return {
        "id": existing.id,
        "app_package_id": existing.app_package_id,
        "user_id": existing.user_id,
        "user_email": user.email,
        "rating": existing.rating,
        "text": existing.text,
        "created_at": existing.created_at,
        "updated_at": existing.updated_at,
    }


@router.delete("/{pkg_id}/reviews", status_code=204)
async def delete_review(
    pkg_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    res = await session.execute(
        select(AppReview).where(
            AppReview.app_package_id == pkg_id,
            AppReview.user_id == user.id,
        )
    )
    r = res.scalar_one_or_none()
    if r is not None:
        await session.delete(r)
        await session.commit()


# ── Per-workspace installations ──────────────────────────────────────────────

async def _require_ws_member(
    ws_id: UUID, user: User, session: AsyncSession, moderator_only: bool = False
) -> WorkspaceMember:
    r = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    m = r.scalar_one_or_none()
    if m is None:
        if not _has_perm(user, "users.view"):
            raise HTTPException(403, "Not a member of this workspace")
        # Admin bypass: construct a phantom membership for return compat
        return WorkspaceMember(workspace_id=ws_id, user_id=user.id, role="moderator")
    if moderator_only and m.role not in (WsRole.OWNER.value, WsRole.MODERATOR.value):
        raise HTTPException(403, "Только владелец или модератор могут менять состав приложений")
    return m


@ws_apps_router.get("/{ws_id}/apps", response_model=list[AppInstallationRead])
async def list_installations(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    await _require_ws_member(ws_id, user, session)
    res = await session.execute(
        select(AppInstallation).where(AppInstallation.workspace_id == ws_id)
    )
    rows = list(res.scalars().all())

    # Bulk-load this user's prefs for all installations in the ws in
    # one query instead of N+1.
    inst_ids = [inst.id for inst in rows]
    prefs_map: dict[UUID, dict] = {}
    if inst_ids:
        pref_q = await session.execute(
            select(AppInstallationUserPref).where(
                AppInstallationUserPref.user_id == user.id,
                AppInstallationUserPref.installation_id.in_(inst_ids),
            )
        )
        for p in pref_q.scalars().all():
            prefs_map[p.installation_id] = p.prefs or {}

    out = []
    for inst in rows:
        pkg = await session.get(AppPackage, inst.app_package_id)
        ver = await session.get(AppPackageVersion, inst.version_id)
        out.append({
            "id": inst.id,
            "workspace_id": inst.workspace_id,
            "app_package_id": inst.app_package_id,
            "version_id": inst.version_id,
            "settings": inst.settings,
            "is_enabled": inst.is_enabled,
            "installed_by_user_id": inst.installed_by_user_id,
            "installed_at": inst.installed_at,
            "updated_at": inst.updated_at,
            "package": await _enrich_package(pkg, session) if pkg else None,
            "version": {
                "id": ver.id,
                "app_package_id": ver.app_package_id,
                "version": ver.version,
                "manifest": ver.manifest,
                "bundle_path": ver.bundle_path,
                "changelog": ver.changelog,
                "size_bytes": ver.size_bytes,
                "is_deprecated": ver.is_deprecated,
                "created_at": ver.created_at,
            } if ver else None,
            "user_prefs": prefs_map.get(inst.id, {}),
        })
    return out


@ws_apps_router.put("/{ws_id}/apps/{inst_id}/my-prefs", response_model=dict)
async def update_my_installation_prefs(
    ws_id: UUID,
    inst_id: UUID,
    payload: AppInstallationUserPrefsUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Replace the current user's per-installation UI prefs.

    Any workspace member can call this for themselves — no moderator
    check, because the prefs only affect what THIS user sees. Unknown
    keys are stored as-is; consumers should tolerate missing keys.
    """
    await _require_ws_member(ws_id, user, session)

    # Make sure the installation actually belongs to this workspace —
    # otherwise someone could write prefs for another ws's app.
    inst = await session.get(AppInstallation, inst_id)
    if inst is None or inst.workspace_id != ws_id:
        raise HTTPException(404, "Установка не найдена в этом пространстве")

    res = await session.execute(
        select(AppInstallationUserPref).where(
            AppInstallationUserPref.user_id == user.id,
            AppInstallationUserPref.installation_id == inst_id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        row = AppInstallationUserPref(
            user_id=user.id,
            installation_id=inst_id,
            prefs=payload.prefs or {},
        )
        session.add(row)
    else:
        row.prefs = payload.prefs or {}
        row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"prefs": row.prefs}


@ws_apps_router.post("/{ws_id}/apps", response_model=AppInstallationRead, status_code=201)
async def install_app(
    ws_id: UUID,
    payload: AppInstallRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    await _require_ws_member(ws_id, user, session, moderator_only=True)

    pkg = await session.get(AppPackage, payload.app_package_id)
    if pkg is None or pkg.approval_status != AppApprovalStatus.APPROVED.value:
        raise HTTPException(404, "Приложение недоступно для установки")

    # Private apps can only be installed in their owner workspace
    if not pkg.is_public and pkg.owner_workspace_id != ws_id:
        raise HTTPException(403, "Это приватное приложение недоступно в данном пространстве")

    # Already installed?
    ex = await session.execute(
        select(AppInstallation).where(
            AppInstallation.workspace_id == ws_id,
            AppInstallation.app_package_id == payload.app_package_id,
        )
    )
    if ex.scalar_one_or_none():
        raise HTTPException(409, "Приложение уже установлено")

    # Resolve version
    if payload.version_id:
        ver = await session.get(AppPackageVersion, payload.version_id)
        if ver is None or ver.app_package_id != pkg.id:
            raise HTTPException(404, "Version not found")
    else:
        vq = await session.execute(
            select(AppPackageVersion)
            .where(
                AppPackageVersion.app_package_id == pkg.id,
                AppPackageVersion.is_deprecated.is_(False),
            )
            .order_by(AppPackageVersion.created_at.desc())
            .limit(1)
        )
        ver = vq.scalar_one_or_none()
        if ver is None:
            raise HTTPException(400, "У приложения нет подходящих версий")

    # Manifest-declared RBAC: refuse install when the installer doesn't
    # meet role_required / permissions_required. Admins bypass.
    is_admin = _has_perm(user, "users.view")
    if not is_admin:
        manifest = ver.manifest or {}
        user_perms = set(user.permissions or [])
        required_perms = set(manifest.get("permissions_required") or [])
        missing_perms = required_perms - user_perms
        if missing_perms:
            raise HTTPException(
                403,
                f"Не хватает прав для установки: {sorted(missing_perms)}",
            )
        required_roles = manifest.get("role_required") or []
        if required_roles:
            # Check installer's role in THIS workspace is among required
            mem_q = await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws_id,
                    WorkspaceMember.user_id == user.id,
                )
            )
            member = mem_q.scalar_one_or_none()
            member_role = member.role if member else None
            if member_role not in required_roles:
                raise HTTPException(
                    403,
                    f"Требуется роль в пространстве: {required_roles}",
                )

    inst = AppInstallation(
        workspace_id=ws_id,
        app_package_id=pkg.id,
        version_id=ver.id,
        settings=payload.settings,
        installed_by_user_id=user.id,
    )
    session.add(inst)
    await session.flush()  # get inst.id before the audit row references it
    _audit(
        session,
        workspace_id=ws_id,
        installation_id=inst.id,
        app_package_id=pkg.id,
        package_name=pkg.name,
        action=AppInstallationAuditAction.INSTALLED,
        to_version=ver.version,
        user=user,
    )
    await session.commit()
    await session.refresh(inst)
    return {
        "id": inst.id,
        "workspace_id": inst.workspace_id,
        "app_package_id": inst.app_package_id,
        "version_id": inst.version_id,
        "settings": inst.settings,
        "is_enabled": inst.is_enabled,
        "installed_by_user_id": inst.installed_by_user_id,
        "installed_at": inst.installed_at,
        "updated_at": inst.updated_at,
        "package": await _enrich_package(pkg, session),
        "version": None,  # caller can re-fetch if needed
    }


@ws_apps_router.patch("/{ws_id}/apps/{inst_id}", response_model=AppInstallationRead)
async def update_installation(
    ws_id: UUID,
    inst_id: UUID,
    payload: AppInstallUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    await _require_ws_member(ws_id, user, session, moderator_only=True)

    inst = await session.get(AppInstallation, inst_id)
    if inst is None or inst.workspace_id != ws_id:
        raise HTTPException(404, "Installation not found")

    pkg_before = await session.get(AppPackage, inst.app_package_id)
    pkg_name = pkg_before.name if pkg_before else None

    if payload.version_id is not None and payload.version_id != inst.version_id:
        ver = await session.get(AppPackageVersion, payload.version_id)
        if ver is None or ver.app_package_id != inst.app_package_id:
            raise HTTPException(404, "Version not found")
        prev_ver = await session.get(AppPackageVersion, inst.version_id)
        _audit(
            session,
            workspace_id=ws_id,
            installation_id=inst.id,
            app_package_id=inst.app_package_id,
            package_name=pkg_name,
            action=AppInstallationAuditAction.VERSION_CHANGED,
            from_version=prev_ver.version if prev_ver else None,
            to_version=ver.version,
            user=user,
        )
        inst.version_id = payload.version_id

    if payload.settings is not None and payload.settings != inst.settings:
        # Record only which keys changed (not the values — they may
        # contain secrets). Lets an admin see "someone edited api_token"
        # without exposing the token itself.
        before = inst.settings or {}
        after = payload.settings
        changed = sorted(
            set(before.keys()) | set(after.keys())
            if not isinstance(before, dict) or not isinstance(after, dict)
            else {k for k in (set(before) | set(after)) if before.get(k) != after.get(k)}
        )
        _audit(
            session,
            workspace_id=ws_id,
            installation_id=inst.id,
            app_package_id=inst.app_package_id,
            package_name=pkg_name,
            action=AppInstallationAuditAction.SETTINGS_CHANGED,
            details={"changed_keys": changed},
            user=user,
        )
        inst.settings = payload.settings

    if payload.is_enabled is not None and payload.is_enabled != inst.is_enabled:
        _audit(
            session,
            workspace_id=ws_id,
            installation_id=inst.id,
            app_package_id=inst.app_package_id,
            package_name=pkg_name,
            action=(
                AppInstallationAuditAction.ENABLED
                if payload.is_enabled
                else AppInstallationAuditAction.DISABLED
            ),
            user=user,
        )
        inst.is_enabled = payload.is_enabled

    await session.commit()
    await session.refresh(inst)

    pkg = await session.get(AppPackage, inst.app_package_id)
    return {
        "id": inst.id,
        "workspace_id": inst.workspace_id,
        "app_package_id": inst.app_package_id,
        "version_id": inst.version_id,
        "settings": inst.settings,
        "is_enabled": inst.is_enabled,
        "installed_by_user_id": inst.installed_by_user_id,
        "installed_at": inst.installed_at,
        "updated_at": inst.updated_at,
        "package": await _enrich_package(pkg, session) if pkg else None,
        "version": None,
    }


class _TokenResponse(BaseModel):
    token: str
    expires_at: datetime
    installation_id: UUID
    workspace_id: UUID
    permissions: list[str]


@ws_apps_router.post("/{ws_id}/apps/{inst_id}/token", response_model=_TokenResponse)
async def get_installation_token(
    ws_id: UUID,
    inst_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Issue a short-lived token the UI can hand to the app's iframe.

    Scoped to the triple (workspace, installation, user). Grants the
    intersection of user.permissions and the manifest's declared
    permissions_required (so even a privileged user can't escalate
    an app's scope by asking for more).
    """
    await _require_ws_member(ws_id, user, session)

    inst = await session.get(AppInstallation, inst_id)
    if inst is None or inst.workspace_id != ws_id:
        raise HTTPException(404, "Installation not found")
    if not inst.is_enabled:
        raise HTTPException(400, "Приложение отключено")
    ver = await session.get(AppPackageVersion, inst.version_id)
    if ver is None:
        raise HTTPException(500, "Version row missing")

    required = set((ver.manifest or {}).get("permissions_required") or [])
    user_perms = set(user.permissions or [])
    granted = sorted(required & user_perms) if required else []

    token, exp = issue_installation_token(
        user_id=user.id,
        workspace_id=ws_id,
        installation_id=inst_id,
        granted_permissions=granted,
    )
    return {
        "token": token,
        "expires_at": exp,
        "installation_id": inst_id,
        "workspace_id": ws_id,
        "permissions": granted,
    }


@router.get("/builtins/jira/ping")
async def jira_ping(
    installation_id: UUID,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Verify Jira settings by hitting /rest/api/2/myself."""
    import base64
    import httpx

    inst = await session.get(AppInstallation, installation_id)
    if inst is None or str(inst.id) != claims["inst"]:
        raise HTTPException(403, "Installation mismatch")
    s = inst.settings or {}
    url = (s.get("jira_url") or "").rstrip("/")
    email = s.get("api_email")
    token = s.get("api_token")
    if not url or not email or not token:
        raise HTTPException(400, "Не заполнены jira_url / api_email / api_token")

    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{url}/rest/api/2/myself",
                headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
            )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:500])
        data = r.json()
        return {"user": data.get("displayName") or data.get("emailAddress") or "OK"}
    except httpx.RequestError as e:
        raise HTTPException(502, f"Jira недоступна: {e}") from e


class _JiraCreateRequest(BaseModel):
    installation_id: UUID
    summary: str
    description: str | None = None
    priority: str | None = None


@router.post("/builtins/jira/create")
async def jira_create_manual(
    payload: _JiraCreateRequest,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Create a Jira issue manually from the iframe."""
    import base64
    import httpx

    inst = await session.get(AppInstallation, payload.installation_id)
    if inst is None or str(inst.id) != claims["inst"]:
        raise HTTPException(403, "Installation mismatch")
    s = inst.settings or {}
    url = (s.get("jira_url") or "").rstrip("/")
    project = s.get("project_key")
    issue_type = s.get("default_issue_type") or "Bug"
    email = s.get("api_email")
    token = s.get("api_token")
    if not url or not project or not email or not token:
        raise HTTPException(400, "Не все настройки Jira заполнены")
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()

    body = {
        "fields": {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": payload.summary,
            "description": payload.description or "",
            "priority": {"name": payload.priority or "Medium"},
        }
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{url}/rest/api/2/issue",
            headers={
                "Authorization": f"Basic {creds}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    data = r.json()
    return {
        "key": data.get("key"),
        "url": f"{url}/browse/{data.get('key')}",
    }


# ── AlfaGen Sandbox proxy ────────────────────────────────────────────────────
#
# Iframe can't talk to AlfaGen directly — it lives on the corporate
# network only, and we don't want the UUID token exposed to the
# browser. These endpoints use the installation's stored settings to
# make authenticated calls on behalf of the iframe.


async def _alfagen_inst(
    installation_id: UUID,
    claims: dict,
    session: AsyncSession,
) -> AppInstallation:
    inst = await session.get(AppInstallation, installation_id)
    if inst is None or str(inst.id) != claims["inst"]:
        raise HTTPException(403, "Installation mismatch")
    if not inst.is_enabled:
        raise HTTPException(400, "Приложение отключено")
    s = inst.settings or {}
    if not s.get("api_url") or not s.get("api_token"):
        raise HTTPException(400, "Не заполнены api_url / api_token")
    return inst


def _alfagen_headers(settings: dict) -> dict[str, str]:
    import uuid as _uuid
    return {
        "Authorization": f"Bearer {settings.get('api_token') or ''}",
        "systemId": settings.get("system_id") or "sanduser",
        "messageId": str(_uuid.uuid4()),
    }


@router.get("/builtins/alfagen/ping")
async def alfagen_ping(
    installation_id: UUID,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Connectivity + models list in one call."""
    import httpx

    inst = await _alfagen_inst(installation_id, claims, session)
    s = inst.settings or {}
    url = s["api_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{url}/internal/llm/v1/models",
                headers=_alfagen_headers(s),
            )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:500])
        data = r.json()
        models = data.get("data") or data.get("models") or []
        return {"ok": True, "models": models}
    except httpx.RequestError as e:
        raise HTTPException(502, f"AlfaGen недоступен: {e}") from e


@router.get("/builtins/alfagen/models")
async def alfagen_models(
    installation_id: UUID,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Pass-through of /internal/llm/v1/models."""
    import httpx

    inst = await _alfagen_inst(installation_id, claims, session)
    s = inst.settings or {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{s['api_url'].rstrip('/')}/internal/llm/v1/models",
            headers=_alfagen_headers(s),
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


class _AlfaChatReq(BaseModel):
    model: str
    messages: list[dict]
    n: int = 1
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    tools: list[dict] | None = None
    response_format: dict | None = None


@router.post("/builtins/alfagen/chat")
async def alfagen_chat_proxy(
    installation_id: UUID,
    payload: _AlfaChatReq,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    import httpx

    inst = await _alfagen_inst(installation_id, claims, session)
    s = inst.settings or {}
    body = payload.model_dump(exclude_none=True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{s['api_url'].rstrip('/')}/internal/llm/v1/chat/completions",
            headers={**_alfagen_headers(s), "Content-Type": "application/json"},
            json=body,
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


@router.post("/builtins/alfagen/upload")
async def alfagen_upload(
    installation_id: UUID,
    file: UploadFile,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Forward a file upload to AlfaGen. Returns its task_id for polling."""
    import httpx

    inst = await _alfagen_inst(installation_id, claims, session)
    s = inst.settings or {}
    content = await file.read()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{s['api_url'].rstrip('/')}/internal/llm/v1/upload-file",
            headers=_alfagen_headers(s),
            files={"file": (file.filename or "upload", content)},
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    data = r.json()
    return {"task_id": data.get("taskId") or data.get("task_id"), "raw": data}


@router.get("/builtins/alfagen/upload-status")
async def alfagen_upload_status(
    installation_id: UUID,
    task_id: str,
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Subscribe to AlfaGen's SSE stream for a single poll cycle.

    AlfaGen emits status frames over SSE; we read up to a short timeout
    and return whatever we've seen. The frontend calls this repeatedly
    until status == "COMPLETED" and file_id is set.
    """
    import httpx

    inst = await _alfagen_inst(installation_id, claims, session)
    s = inst.settings or {}
    url = f"{s['api_url'].rstrip('/')}/internal/llm/v1/upload-file/{task_id}/sse"
    last_status = "PROCESSING"
    file_id = None
    error = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream("GET", url, headers=_alfagen_headers(s)) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(resp.status_code, await resp.aread())
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    import json as _json
                    try:
                        frame = _json.loads(line[5:].strip())
                    except _json.JSONDecodeError:
                        continue
                    d = frame.get("data") or {}
                    last_status = d.get("status") or last_status
                    if d.get("fileId"):
                        file_id = d["fileId"]
                    if d.get("error"):
                        error = d["error"]
                    if last_status in ("COMPLETED", "FAILED"):
                        break
    except httpx.RequestError as e:
        raise HTTPException(502, f"SSE недоступен: {e}") from e

    return {"status": last_status, "file_id": file_id, "error": error}


@router.get("/me/context")
async def app_context(
    claims: Annotated[dict, Depends(require_installation_token)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """What the iframe calls on startup to discover who it is.

    Returns the user_id, workspace_id, installation_id, current settings
    and granted permissions so the app can render its UI without
    knowing Markov's internals.
    """
    inst_id = UUID(claims["inst"])
    inst = await session.get(AppInstallation, inst_id)
    if inst is None:
        raise HTTPException(404, "Installation not found")
    return {
        "user_id": claims["sub"],
        "workspace_id": claims["wsid"],
        "installation_id": claims["inst"],
        "permissions": claims.get("perms", []),
        "settings": inst.settings or {},
    }


@ws_apps_router.delete("/{ws_id}/apps/{inst_id}", status_code=204)
async def uninstall_app(
    ws_id: UUID,
    inst_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    await _require_ws_member(ws_id, user, session, moderator_only=True)
    inst = await session.get(AppInstallation, inst_id)
    if inst is None or inst.workspace_id != ws_id:
        raise HTTPException(404, "Installation not found")
    pkg = await session.get(AppPackage, inst.app_package_id)
    ver = await session.get(AppPackageVersion, inst.version_id)
    _audit(
        session,
        workspace_id=ws_id,
        installation_id=inst.id,
        app_package_id=inst.app_package_id,
        package_name=pkg.name if pkg else None,
        action=AppInstallationAuditAction.UNINSTALLED,
        from_version=ver.version if ver else None,
        user=user,
    )
    await session.delete(inst)
    await session.commit()


# ── History / audit log ──────────────────────────────────────────────────────


@ws_apps_router.get("/{ws_id}/apps-history", response_model=list[dict])
async def list_apps_history(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    limit: int = 200,
) -> list[dict]:
    """Audit log of install/update/uninstall events for the workspace.

    Any member can read (it's their history too). ``limit`` caps how many
    rows come back; we sort newest-first so pagination via offset is easy
    to add later if the table grows.
    """
    await _require_ws_member(ws_id, user, session)
    res = await session.execute(
        select(AppInstallationAudit)
        .where(AppInstallationAudit.workspace_id == ws_id)
        .order_by(AppInstallationAudit.created_at.desc())
        .limit(max(1, min(limit, 1000)))
    )
    out: list[dict] = []
    for row in res.scalars().all():
        out.append(
            {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "app_package_id": row.app_package_id,
                "installation_id": row.installation_id,
                "package_name": row.package_name,
                "action": row.action,
                "from_version": row.from_version,
                "to_version": row.to_version,
                "details": row.details,
                "user_id": row.user_id,
                "user_email": row.user_email,
                "created_at": row.created_at,
            }
        )
    return out
