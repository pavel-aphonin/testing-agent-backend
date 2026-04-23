"""Manifest + API schemas for extension apps."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Manifest (contents of manifest.json) ─────────────────────────────────────

class ManifestSlot(BaseModel):
    """Where the app shows a UI entry point.

    slot: which region of the host UI to inject into
        - sidebar              → new menu item
        - top_bar              → header-right action
        - corner               → floating button in bottom-right
        - run_actions          → button on the run detail page
        - workspace_settings   → tab in workspace settings
        ...extensible; unknown slots are ignored client-side

    path: iframe URL (relative to the app bundle root), e.g. "index.html".
    """

    slot: str
    label: str
    icon: str | None = None
    path: str = "index.html"


class ManifestSettingField(BaseModel):
    code: str
    name: str
    # "string"  → single-line Input
    # "text"    → multi-line Input.TextArea (used for prompts, long descriptions)
    # "secret"  → Input.Password (never returned by /installations read)
    # "enum"    → Select with enum_values
    type: Literal["string", "text", "number", "boolean", "secret", "enum"] = "string"
    enum_values: list[str] | None = None
    required: bool = False
    default: Any | None = None
    # Short human-readable label for the "?" tooltip next to the field
    # name. Keep it simple — the whole point is that beginner users
    # should be able to read it and know what to do.
    description: str | None = None
    # Optional section for visual grouping on the install drawer.
    # Fields without a group render at the top, grouped fields render
    # under collapsible sections. Keeps the form scannable when an app
    # has more than a handful of settings.
    group: str | None = None


class ManifestHook(BaseModel):
    """Backend hook.

    event: one of the documented events — "defect.created", "run.completed",
           "screen.discovered", etc. See docs for the full list.
    handler: dotted path within the logic/ folder, e.g.
             "handlers.on_defect". Interpreted server-side (Phase 2).
    """

    event: str
    handler: str


class ManifestScreenshot(BaseModel):
    """Gallery image shown on the app detail page. Paths are relative
    to the bundle root; filled automatically by the extractor from
    everything under screenshots/."""

    path: str
    caption: str | None = None


class AppManifest(BaseModel):
    code: str = Field(..., pattern=r"^[a-z][a-z0-9_-]*$", min_length=1, max_length=100)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str = "utility"
    author: str | None = None

    # Manifest-level RBAC declarations. The host enforces these on top of
    # the workspace-level "who can install" policy.
    permissions_required: list[str] = Field(default_factory=list)
    role_required: list[str] = Field(default_factory=list)

    ui_slots: list[ManifestSlot] = Field(default_factory=list)
    settings_schema: list[ManifestSettingField] = Field(default_factory=list)
    hooks: list[ManifestHook] = Field(default_factory=list)
    screenshots: list[ManifestScreenshot] = Field(default_factory=list)
    # Optional free-form release notes for THIS version. Shown in the
    # detail page's version list. If absent the extractor tries to read
    # CHANGELOG.md from the bundle root.
    changelog: str | None = None


# ── API schemas ──────────────────────────────────────────────────────────────

class AppPackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    category: str
    author: str | None
    logo_path: str | None
    cover_path: str | None = None
    is_public: bool
    owner_workspace_id: uuid.UUID | None
    approval_status: str
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    # Aggregate fields (filled by query, not columns)
    latest_version: str | None = None
    install_count: int = 0
    avg_rating: float | None = None
    review_count: int = 0


class AppPackageVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_package_id: uuid.UUID
    version: str
    manifest: dict
    bundle_path: str
    changelog: str | None
    size_bytes: int
    is_deprecated: bool
    created_at: datetime


class AppInstallationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    app_package_id: uuid.UUID
    version_id: uuid.UUID
    settings: dict | None
    is_enabled: bool
    installed_by_user_id: uuid.UUID | None
    installed_at: datetime
    updated_at: datetime | None
    # Enriched fields
    package: AppPackageRead | None = None
    version: AppPackageVersionRead | None = None
    # Current user's per-installation UI prefs (free-form JSONB).
    # Known keys today: "hidden_from_sidebar" (bool), "hidden_from_top_bar" (bool).
    # Missing keys = show by default. Only populated on the endpoint that
    # reads installations for the current user.
    user_prefs: dict = {}


class AppInstallationUserPrefsUpdate(BaseModel):
    """Replace the current user's prefs for one installation.

    We do a full replace (not a partial merge) so the client is in
    full control of the object — simpler to reason about than PATCH
    semantics over JSONB.
    """

    prefs: dict


class AppInstallRequest(BaseModel):
    app_package_id: uuid.UUID
    version_id: uuid.UUID | None = None  # defaults to latest non-deprecated
    settings: dict | None = None


class AppInstallUpdate(BaseModel):
    version_id: uuid.UUID | None = None
    settings: dict | None = None
    is_enabled: bool | None = None


class AppApprovalDecision(BaseModel):
    approved: bool
    rejection_reason: str | None = None


class AppReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_package_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str = ""
    rating: int
    text: str | None
    created_at: datetime
    updated_at: datetime | None


class AppReviewUpsert(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    text: str | None = None


class AppPublishRequest(BaseModel):
    """Metadata for the uploaded bundle that can't be derived from the ZIP."""

    is_public: bool = False
    owner_workspace_id: uuid.UUID | None = None
    # If true, package is published in "draft" state → the uploader must
    # submit for review separately. If false, it goes straight to
    # approved (admin uploader bypass).
    submit_for_review: bool = True
