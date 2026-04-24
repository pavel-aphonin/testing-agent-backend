"""Dashboards + widgets + per-user access.

Two dashboard flavors:

  - **System** (``is_system=true``): exactly one per workspace, created
    automatically on workspace insert. Cannot be deleted. Only
    moderators of the workspace can edit widgets on it. Its name
    mirrors the workspace name.
  - **User**: any workspace member can create. Author owns it — only
    they can delete it. Visibility / edit rights are granted via
    ``dashboard_permissions`` to specific members.

Widgets carry presentation (type, size, position) + where the data
comes from (``datasource_code`` + ``datasource_params``) + chart
customization (``chart_options``). The backend resolves data-source
codes into ApexCharts-shaped series on demand — the stored widget
blob is pure config.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DashboardPermissionLevel(StrEnum):
    """Access granted by the dashboard owner to a specific user."""

    VIEW = "view"
    EDIT = "edit"


class Dashboard(Base):
    __tablename__ = "dashboards"
    __table_args__ = (
        # At most one system dashboard per workspace. Enforced by a
        # partial unique index in the migration.
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Emoji / short icon code rendered next to the tab name.
    icon: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # NULL for the system dashboard; author's user id for user ones.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Display order inside the workspace's dashboard switcher.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ApexCharts type: "line", "bar", "area", "pie", "donut",
    # "radialBar", "scatter", "heatmap", "treemap", "radar",
    # "polarArea", "boxplot", "candlestick", "rangeBar", "funnel",
    # "mixed", plus our own "table". Not a strict enum so adding types
    # doesn't require a migration.
    widget_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Виджет")

    # What data to show. ``datasource_code`` picks a handler on the
    # backend; ``datasource_params`` are the knobs for that handler
    # (date range, limit, group-by, workspace_id scope, etc.).
    datasource_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    datasource_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Free-form ApexCharts options overlay — colors, stroke, axis
    # labels, data labels, legend position, tooltips. Merged on top
    # of per-type defaults at render time.
    chart_options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Grid position, 12-column react-grid-layout convention. Width
    # and height are in grid units, not pixels.
    grid_x: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grid_y: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grid_w: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    grid_h: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class WidgetTemplate(Base):
    """Saved widget preset — a reusable "custom widget".

    Lightweight take on user-created widgets: instead of shipping an
    iframe-sandboxed bundle with manifest/logic, a template is just
    a pinned combination of ``widget_type + datasource + chart_options +
    default size + meta``. Any workspace member can create one from
    any existing widget ("Save as template"); the whole workspace can
    then pick it from the "Add widget" menu.

    A follow-up iteration will add full code widgets (iframe + logic)
    for the rare cases where a datasource + apex type combo isn't
    enough. For now templates cover the 90% case.
    """

    __tablename__ = "widget_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The actual widget config. Same fields as DashboardWidget minus
    # the grid position — that gets defaulted on instantiation.
    widget_type: Mapped[str] = mapped_column(String(30), nullable=False)
    datasource_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    datasource_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chart_options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    default_w: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    default_h: Mapped[int] = mapped_column(Integer, default=4, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class WidgetPackage(Base):
    """Custom widget package — an HTML/JS blob with a manifest.

    Phase 3b of the dashboards epic: workspaces can upload their own
    widget implementations (think "mini-apps for the dashboard"). Each
    package is:

      - Identified by a workspace-local ``code`` slug (``"my-kpi-v1"``).
      - Rendered as an iframe (``srcdoc``-based sandbox) that receives
        ``{widget, data}`` via ``postMessage`` from the parent.
      - Authored by any workspace member with the ``dashboards.manage_packages``
        permission; the author's id is recorded for audit.
      - Toggleable via ``is_active`` — disabled packages don't appear in
        the add-widget menu but existing widget instances keep rendering
        (so taking a package offline doesn't instantly break dashboards).

    The ``manifest`` JSON describes:
      - The data source(s) it understands (an allow-list; the parent
        refuses to pass data from a disallowed source).
      - A human-readable config schema (``{fields: [...]}``) that the
        settings drawer can render as a form instead of raw JSON.

    ``html_source`` is the full HTML document. Kept as text; size capped
    at 256KiB by the API layer. Not hosted as static files — keeping it
    in the DB means workspace admins don't touch the filesystem and the
    same backup/restore path covers everything.
    """

    __tablename__ = "widget_packages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_widget_pkg_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Workspace-scoped unique slug; used in ``chart_options.package_id``
    # references from widget instances. Human-readable but stable.
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(20), nullable=True)
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="0.1.0")
    # JSON manifest: {allowed_sources: [...], config_fields: [...]}. See
    # docstring. The API validates structure but leaves the contents
    # flexible so adding manifest features doesn't require a migration.
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Full HTML document including inline <script>. The iframe loads it
    # via srcdoc, so relative URLs / localStorage don't leak from the
    # parent app. The script talks to us via window.postMessage.
    html_source: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )


class DashboardPermission(Base):
    """Per-user access to a (user-flavor) dashboard.

    The owner always has edit access implicitly. The system dashboard
    does not use this table — its access is derived from workspace
    membership + moderator role.
    """

    __tablename__ = "dashboard_permissions"
    __table_args__ = (
        UniqueConstraint("dashboard_id", "user_id", name="uq_dash_perm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
