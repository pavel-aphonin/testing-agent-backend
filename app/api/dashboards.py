"""Dashboard + widget API.

  GET    /api/workspaces/{ws}/dashboards           list accessible dashboards
  POST   /api/workspaces/{ws}/dashboards           create a user dashboard
  GET    /api/dashboards/{id}                      read one dashboard + widgets
  PATCH  /api/dashboards/{id}                      update meta (name/icon/desc)
  DELETE /api/dashboards/{id}                      delete (owner only; system = refused)

  POST   /api/dashboards/{id}/widgets              add widget
  PATCH  /api/widgets/{wid}                        edit widget (title, settings, etc.)
  PUT    /api/dashboards/{id}/layout               bulk-save grid positions
  DELETE /api/widgets/{wid}                        remove widget

  GET    /api/dashboards/widget-data/{code}        resolve a data source
  GET    /api/dashboards/datasources               list data source metadata

  PUT    /api/dashboards/{id}/permissions/{uid}    grant a user view/edit
  DELETE /api/dashboards/{id}/permissions/{uid}    revoke

Access rules:
- System dashboard: every workspace member sees it. Only workspace
  moderators (``WsRole.MODERATOR``) can edit.
- User dashboard: owner always has edit; other users only if there's
  an explicit grant in ``dashboard_permissions``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_async_session
from app.models.dashboard import (
    Dashboard,
    DashboardPermission,
    DashboardPermissionLevel,
    DashboardWidget,
    WidgetPackage,
    WidgetTemplate,
)
from app.models.user import User
from app.models.workspace import WorkspaceMember, WsRole
from app.services import dashboard_datasources as ds

router = APIRouter(prefix="/api", tags=["dashboards"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dashboard_id: UUID
    widget_type: str
    title: str
    datasource_code: str | None
    datasource_params: dict | None
    chart_options: dict | None
    grid_x: int
    grid_y: int
    grid_w: int
    grid_h: int


class DashboardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    icon: str | None
    is_system: bool
    owner_user_id: UUID | None
    sort_order: int
    can_edit: bool = False


class DashboardFull(DashboardSummary):
    widgets: list[WidgetRead]


class DashboardCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)


class DashboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)


class WidgetCreate(BaseModel):
    widget_type: str = Field(..., min_length=1, max_length=30)
    title: str = Field(default="Виджет", max_length=200)
    datasource_code: str | None = None
    datasource_params: dict | None = None
    chart_options: dict | None = None
    grid_x: int = 0
    grid_y: int = 0
    grid_w: int = 6
    grid_h: int = 4


class WidgetUpdate(BaseModel):
    widget_type: str | None = Field(default=None, min_length=1, max_length=30)
    title: str | None = Field(default=None, max_length=200)
    datasource_code: str | None = None
    datasource_params: dict | None = None
    chart_options: dict | None = None


class LayoutItem(BaseModel):
    id: UUID
    grid_x: int
    grid_y: int
    grid_w: int
    grid_h: int


class LayoutUpdate(BaseModel):
    items: list[LayoutItem]


class PermissionGrant(BaseModel):
    level: DashboardPermissionLevel


# ── Access helpers ───────────────────────────────────────────────────────────


async def _ws_member_role(
    session: AsyncSession, ws_id: UUID, user_id: UUID
) -> WsRole | None:
    r = await session.execute(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return r.scalar_one_or_none()


async def _require_ws_member(
    session: AsyncSession, ws_id: UUID, user: User
) -> WsRole:
    if "users.view" in (user.permissions or []):
        # Admins have implicit moderator access in every workspace.
        return WsRole.MODERATOR
    role = await _ws_member_role(session, ws_id, user.id)
    if role is None:
        raise HTTPException(403, "Вы не состоите в этом рабочем пространстве")
    return role


async def _can_edit_dashboard(
    session: AsyncSession, dash: Dashboard, user: User
) -> bool:
    role = await _ws_member_role(session, dash.workspace_id, user.id)
    is_admin = "users.view" in (user.permissions or [])
    if dash.is_system:
        # System dashboard: admins of the app OR workspace moderators
        # can edit. Regular members see but don't edit.
        return is_admin or role == WsRole.MODERATOR
    # User dashboard: owner always, plus anyone with explicit EDIT grant.
    if dash.owner_user_id == user.id or is_admin:
        return True
    if role is None:
        return False
    grant_q = await session.execute(
        select(DashboardPermission.level).where(
            DashboardPermission.dashboard_id == dash.id,
            DashboardPermission.user_id == user.id,
        )
    )
    lvl = grant_q.scalar_one_or_none()
    return lvl == DashboardPermissionLevel.EDIT.value


async def _can_view_dashboard(
    session: AsyncSession, dash: Dashboard, user: User
) -> bool:
    if await _can_edit_dashboard(session, dash, user):
        return True
    role = await _ws_member_role(session, dash.workspace_id, user.id)
    if role is None:
        return False
    if dash.is_system:
        return True  # every workspace member sees the system dashboard
    if dash.owner_user_id == user.id:
        return True
    grant_q = await session.execute(
        select(DashboardPermission.level).where(
            DashboardPermission.dashboard_id == dash.id,
            DashboardPermission.user_id == user.id,
        )
    )
    return grant_q.scalar_one_or_none() is not None


async def _get_dashboard_or_404(
    session: AsyncSession, dash_id: UUID
) -> Dashboard:
    d = await session.get(Dashboard, dash_id)
    if d is None:
        raise HTTPException(404, "Дашборд не найден")
    return d


async def _serialize_summary(
    session: AsyncSession, d: Dashboard, user: User
) -> DashboardSummary:
    return DashboardSummary(
        id=d.id,
        workspace_id=d.workspace_id,
        name=d.name,
        description=d.description,
        icon=d.icon,
        is_system=d.is_system,
        owner_user_id=d.owner_user_id,
        sort_order=d.sort_order,
        can_edit=await _can_edit_dashboard(session, d, user),
    )


# ── Endpoints: dashboards ────────────────────────────────────────────────────


@router.get("/workspaces/{ws_id}/dashboards", response_model=list[DashboardSummary])
async def list_dashboards(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[DashboardSummary]:
    """Every dashboard the user can see in this workspace — the system
    one plus user ones the user owns or was granted access to."""
    await _require_ws_member(session, ws_id, user)
    # Pull all and filter in Python — the ws's dashboard count is small
    # (usually single digits) so saving a few CPU cycles on the DB isn't
    # worth the SQL complexity.
    r = await session.execute(
        select(Dashboard)
        .where(Dashboard.workspace_id == ws_id)
        .order_by(Dashboard.is_system.desc(), Dashboard.sort_order, Dashboard.created_at)
    )
    out: list[DashboardSummary] = []
    for d in r.scalars().all():
        if await _can_view_dashboard(session, d, user):
            out.append(await _serialize_summary(session, d, user))
    return out


@router.post(
    "/workspaces/{ws_id}/dashboards",
    response_model=DashboardSummary,
    status_code=201,
)
async def create_dashboard(
    ws_id: UUID,
    payload: DashboardCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DashboardSummary:
    """Any workspace member can create a user dashboard. System
    dashboards are created server-side on workspace creation, not via
    this endpoint."""
    await _require_ws_member(session, ws_id, user)
    d = Dashboard(
        workspace_id=ws_id,
        name=payload.name.strip(),
        description=payload.description,
        icon=payload.icon,
        is_system=False,
        owner_user_id=user.id,
        sort_order=1000,  # user dashboards sort after the system one
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return await _serialize_summary(session, d, user)


@router.get("/dashboards/{dash_id}", response_model=DashboardFull)
async def get_dashboard(
    dash_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DashboardFull:
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_view_dashboard(session, d, user):
        raise HTTPException(403, "Нет доступа к этому дашборду")
    w = await session.execute(
        select(DashboardWidget)
        .where(DashboardWidget.dashboard_id == dash_id)
        .order_by(DashboardWidget.grid_y, DashboardWidget.grid_x)
    )
    widgets = [WidgetRead.model_validate(wi) for wi in w.scalars().all()]
    summary = await _serialize_summary(session, d, user)
    return DashboardFull(**summary.model_dump(), widgets=widgets)


@router.patch("/dashboards/{dash_id}", response_model=DashboardSummary)
async def update_dashboard(
    dash_id: UUID,
    payload: DashboardUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> DashboardSummary:
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование этого дашборда")
    if d.is_system and payload.name is not None:
        # System dashboard name mirrors the workspace name — don't let
        # moderators rename it away from that invariant.
        raise HTTPException(
            400,
            "Название системного дашборда наследуется от названия пространства",
        )
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(d, k, v)
    await session.commit()
    await session.refresh(d)
    return await _serialize_summary(session, d, user)


@router.delete("/dashboards/{dash_id}", status_code=204)
async def delete_dashboard(
    dash_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    d = await _get_dashboard_or_404(session, dash_id)
    if d.is_system:
        raise HTTPException(400, "Системный дашборд нельзя удалить")
    is_admin = "users.view" in (user.permissions or [])
    if not (d.owner_user_id == user.id or is_admin):
        raise HTTPException(403, "Удалить дашборд может только его автор")
    await session.delete(d)
    await session.commit()


# ── Endpoints: widgets ───────────────────────────────────────────────────────


@router.post(
    "/dashboards/{dash_id}/widgets", response_model=WidgetRead, status_code=201
)
async def add_widget(
    dash_id: UUID,
    payload: WidgetCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetRead:
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование этого дашборда")
    w = DashboardWidget(
        dashboard_id=dash_id, **payload.model_dump()
    )
    session.add(w)
    await session.commit()
    await session.refresh(w)
    return WidgetRead.model_validate(w)


@router.patch("/widgets/{widget_id}", response_model=WidgetRead)
async def update_widget(
    widget_id: UUID,
    payload: WidgetUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetRead:
    w = await session.get(DashboardWidget, widget_id)
    if w is None:
        raise HTTPException(404, "Виджет не найден")
    d = await session.get(Dashboard, w.dashboard_id)
    if d is None or not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    await session.commit()
    await session.refresh(w)
    return WidgetRead.model_validate(w)


@router.delete("/widgets/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    w = await session.get(DashboardWidget, widget_id)
    if w is None:
        raise HTTPException(404, "Виджет не найден")
    d = await session.get(Dashboard, w.dashboard_id)
    if d is None or not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование")
    await session.delete(w)
    await session.commit()


@router.put("/dashboards/{dash_id}/layout", response_model=list[WidgetRead])
async def save_layout(
    dash_id: UUID,
    payload: LayoutUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[WidgetRead]:
    """Bulk-save widget positions after a drag/resize session. The
    grid emits a single payload with the new shape of every widget —
    we update them all in one transaction."""
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование")

    ids = {item.id for item in payload.items}
    r = await session.execute(
        select(DashboardWidget).where(
            DashboardWidget.dashboard_id == dash_id,
            DashboardWidget.id.in_(ids),
        )
    )
    widgets = {w.id: w for w in r.scalars().all()}
    for item in payload.items:
        w = widgets.get(item.id)
        if w is None:
            continue
        w.grid_x, w.grid_y, w.grid_w, w.grid_h = (
            item.grid_x, item.grid_y, item.grid_w, item.grid_h,
        )
    await session.commit()
    # Return refreshed list in the caller's order
    return [WidgetRead.model_validate(widgets[i.id]) for i in payload.items if i.id in widgets]


# ── Endpoints: data sources ──────────────────────────────────────────────────
# Routed under /api/widgets/... so the path doesn't collide with the
# parametric /dashboards/{dash_id} match (FastAPI tries routes in
# registration order; putting these on a different prefix is cleaner
# than rearranging the big CRUD block).


@router.get("/widgets/datasources")
async def list_datasources(
    _user: Annotated[User, Depends(current_active_user)],
) -> dict:
    """Returns the datasource registry plus group labels so the
    frontend dropdown can render an ``<optgroup>``-style list."""
    return {
        "groups": ds.list_datasource_groups(),
        "items": ds.list_datasource_metadata(),
    }


@router.get("/widgets/{widget_id}/data")
async def get_widget_data(
    widget_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict[str, Any]:
    """Resolve the widget's configured data source against its dashboard's
    workspace. Returns an empty payload (not a 404) when the widget has
    no source — UI renders the "нечего отрисовать" state."""
    w = await session.get(DashboardWidget, widget_id)
    if w is None:
        raise HTTPException(404, "Виджет не найден")
    d = await session.get(Dashboard, w.dashboard_id)
    if d is None or not await _can_view_dashboard(session, d, user):
        raise HTTPException(403, "Нет доступа")
    if not w.datasource_code:
        return {"categories": [], "series": [{"name": "—", "data": []}]}
    return await ds.resolve(
        w.datasource_code, d.workspace_id, w.datasource_params, session
    )


# ── Endpoints: widget templates ──────────────────────────────────────────────


class WidgetTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    author_user_id: UUID | None
    name: str
    description: str | None
    icon: str | None
    widget_type: str
    datasource_code: str | None
    datasource_params: dict | None
    chart_options: dict | None
    default_w: int
    default_h: int
    created_at: datetime


class WidgetTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)
    widget_type: str = Field(..., min_length=1, max_length=30)
    datasource_code: str | None = None
    datasource_params: dict | None = None
    chart_options: dict | None = None
    default_w: int = 6
    default_h: int = 4


class WidgetTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)
    widget_type: str | None = Field(default=None, min_length=1, max_length=30)
    datasource_code: str | None = None
    datasource_params: dict | None = None
    chart_options: dict | None = None
    default_w: int | None = None
    default_h: int | None = None


@router.get(
    "/workspaces/{ws_id}/widget-templates",
    response_model=list[WidgetTemplateRead],
)
async def list_templates(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[WidgetTemplate]:
    """Every template the user can see — right now "shared with this
    workspace" == "everyone in the workspace". Personal-only templates
    can come in a later iteration via a ``scope`` column."""
    await _require_ws_member(session, ws_id, user)
    r = await session.execute(
        select(WidgetTemplate)
        .where(WidgetTemplate.workspace_id == ws_id)
        .order_by(WidgetTemplate.created_at.desc())
    )
    return list(r.scalars().all())


@router.post(
    "/workspaces/{ws_id}/widget-templates",
    response_model=WidgetTemplateRead,
    status_code=201,
)
async def create_template(
    ws_id: UUID,
    payload: WidgetTemplateCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetTemplate:
    await _require_ws_member(session, ws_id, user)
    t = WidgetTemplate(
        workspace_id=ws_id,
        author_user_id=user.id,
        **payload.model_dump(),
    )
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return t


@router.patch("/widget-templates/{template_id}", response_model=WidgetTemplateRead)
async def update_template(
    template_id: UUID,
    payload: WidgetTemplateUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetTemplate:
    t = await session.get(WidgetTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Шаблон не найден")
    # Any workspace member can edit — templates are team assets, not
    # personal. Author bias: only author can delete (below).
    await _require_ws_member(session, t.workspace_id, user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(t, k, v)
    await session.commit()
    await session.refresh(t)
    return t


@router.delete("/widget-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    t = await session.get(WidgetTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Шаблон не найден")
    is_admin = "users.view" in (user.permissions or [])
    if t.author_user_id != user.id and not is_admin:
        raise HTTPException(403, "Удалить шаблон может только автор")
    await session.delete(t)
    await session.commit()


@router.post(
    "/dashboards/{dash_id}/widgets/from-template/{template_id}",
    response_model=WidgetRead,
    status_code=201,
)
async def add_widget_from_template(
    dash_id: UUID,
    template_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    grid_x: int = 0,
    grid_y: int = 1000,  # append by default; RGL compacts
) -> WidgetRead:
    """Materialize a dashboard widget from a saved template."""
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_edit_dashboard(session, d, user):
        raise HTTPException(403, "Нет прав на редактирование")
    t = await session.get(WidgetTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Шаблон не найден")
    if t.workspace_id != d.workspace_id:
        # Don't allow cross-workspace template leakage. If a global
        # "widget marketplace" lands later it'll be its own endpoint.
        raise HTTPException(
            400, "Шаблон принадлежит другому рабочему пространству"
        )
    w = DashboardWidget(
        dashboard_id=dash_id,
        widget_type=t.widget_type,
        title=t.name,
        datasource_code=t.datasource_code,
        datasource_params=t.datasource_params,
        chart_options=t.chart_options,
        grid_x=grid_x,
        grid_y=grid_y,
        grid_w=t.default_w,
        grid_h=t.default_h,
    )
    session.add(w)
    await session.commit()
    await session.refresh(w)
    return WidgetRead.model_validate(w)


# ── Endpoints: permissions (user dashboards only) ────────────────────────────


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    user_email: str | None
    level: str


@router.get("/dashboards/{dash_id}/permissions", response_model=list[PermissionRead])
async def list_permissions(
    dash_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[PermissionRead]:
    """Everyone who got an explicit grant on this dashboard. The owner
    isn't included — ownership is stored on the dashboard itself."""
    d = await _get_dashboard_or_404(session, dash_id)
    if not await _can_view_dashboard(session, d, user):
        raise HTTPException(403, "Нет доступа")
    r = await session.execute(
        select(DashboardPermission.user_id, DashboardPermission.level, User.email)
        .join(User, User.id == DashboardPermission.user_id)
        .where(DashboardPermission.dashboard_id == dash_id)
    )
    return [
        PermissionRead(user_id=uid, user_email=email, level=level)
        for uid, level, email in r.all()
    ]


@router.put("/dashboards/{dash_id}/permissions/{uid}", status_code=204)
async def grant_permission(
    dash_id: UUID,
    uid: UUID,
    payload: PermissionGrant,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    d = await _get_dashboard_or_404(session, dash_id)
    if d.is_system:
        raise HTTPException(400, "Доступ к системному дашборду регулируется ролями")
    is_admin = "users.view" in (user.permissions or [])
    if d.owner_user_id != user.id and not is_admin:
        raise HTTPException(403, "Только автор может выдавать доступ")
    # Target must be a member of the same workspace.
    target_role = await _ws_member_role(session, d.workspace_id, uid)
    if target_role is None:
        raise HTTPException(
            400, "Пользователь не состоит в рабочем пространстве этого дашборда"
        )
    existing_q = await session.execute(
        select(DashboardPermission).where(
            DashboardPermission.dashboard_id == dash_id,
            DashboardPermission.user_id == uid,
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is None:
        session.add(DashboardPermission(
            dashboard_id=dash_id, user_id=uid, level=payload.level.value
        ))
    else:
        existing.level = payload.level.value
    await session.commit()


@router.delete("/dashboards/{dash_id}/permissions/{uid}", status_code=204)
async def revoke_permission(
    dash_id: UUID,
    uid: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    d = await _get_dashboard_or_404(session, dash_id)
    is_admin = "users.view" in (user.permissions or [])
    if d.owner_user_id != user.id and not is_admin:
        raise HTTPException(403, "Только автор может отзывать доступ")
    r = await session.execute(
        select(DashboardPermission).where(
            DashboardPermission.dashboard_id == dash_id,
            DashboardPermission.user_id == uid,
        )
    )
    row = r.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


# ── Endpoints: widget packages (Phase 3b) ────────────────────────────────────


# Conservative size guard: fits a comfortable D3/Apex-ish bundle with
# inlined CSS, doesn't let a workspace stuff multi-megabyte assets into
# the DB. Enforced at create/update time; the DB column itself is TEXT
# (unbounded).
MAX_HTML_SIZE = 256 * 1024


class WidgetPackageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    author_user_id: UUID | None
    code: str
    name: str
    description: str | None
    icon: str | None
    version: str
    manifest: dict
    is_active: bool
    created_at: datetime
    # html_source intentionally omitted here — it's large and only
    # needed when actually rendering a widget; fetch via GET /…/source.


class WidgetPackageSource(BaseModel):
    id: UUID
    code: str
    version: str
    manifest: dict
    html_source: str


class WidgetPackageCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9\-_]+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)
    version: str = Field(default="0.1.0", max_length=40)
    manifest: dict = Field(default_factory=dict)
    html_source: str
    is_active: bool = True


class WidgetPackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=20)
    version: str | None = Field(default=None, max_length=40)
    manifest: dict | None = None
    html_source: str | None = None
    is_active: bool | None = None


def _require_html_size(html: str) -> None:
    if len(html.encode("utf-8")) > MAX_HTML_SIZE:
        raise HTTPException(
            413,
            f"HTML слишком большой ({len(html)} байт); максимум {MAX_HTML_SIZE}",
        )


@router.get(
    "/workspaces/{ws_id}/widget-packages",
    response_model=list[WidgetPackageRead],
)
async def list_widget_packages(
    ws_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    only_active: bool = Query(default=False),
) -> list[WidgetPackage]:
    """Every package in the workspace. ``only_active=true`` drops the
    disabled ones — useful for the "add widget" menu; the admin page
    leaves it false to show the whole list."""
    await _require_ws_member(session, ws_id, user)
    q = select(WidgetPackage).where(WidgetPackage.workspace_id == ws_id)
    if only_active:
        q = q.where(WidgetPackage.is_active.is_(True))
    q = q.order_by(WidgetPackage.created_at.desc())
    r = await session.execute(q)
    return list(r.scalars().all())


@router.post(
    "/workspaces/{ws_id}/widget-packages",
    response_model=WidgetPackageRead,
    status_code=201,
)
async def create_widget_package(
    ws_id: UUID,
    payload: WidgetPackageCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetPackage:
    """Moderators of the workspace can publish packages. Size-capped at
    256 KiB to keep things DB-friendly; larger bundles should use an
    external CDN + ``manifest.script_url`` (not yet implemented)."""
    role = await _ws_member_role(session, ws_id, user.id)
    if role != WsRole.MODERATOR and "users.view" not in (user.permissions or []):
        raise HTTPException(403, "Только модератор пространства может публиковать пакеты")
    _require_html_size(payload.html_source)

    p = WidgetPackage(
        workspace_id=ws_id,
        author_user_id=user.id,
        **payload.model_dump(),
    )
    session.add(p)
    try:
        await session.commit()
    except Exception:  # UniqueConstraint on (workspace_id, code)
        await session.rollback()
        raise HTTPException(409, "Код пакета уже занят в этом пространстве")
    await session.refresh(p)
    return p


@router.get(
    "/widget-packages/{package_id}/source",
    response_model=WidgetPackageSource,
)
async def get_widget_package_source(
    package_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetPackageSource:
    """Return the full HTML + manifest — what the iframe srcdoc needs.

    Any workspace member can fetch it (it's already visible via their
    dashboards). Not cached at this layer; the iframe's browser cache
    takes care of that in practice via the package_id in the URL."""
    p = await session.get(WidgetPackage, package_id)
    if p is None:
        raise HTTPException(404, "Пакет не найден")
    await _require_ws_member(session, p.workspace_id, user)
    return WidgetPackageSource(
        id=p.id,
        code=p.code,
        version=p.version,
        manifest=p.manifest or {},
        html_source=p.html_source,
    )


@router.patch(
    "/widget-packages/{package_id}",
    response_model=WidgetPackageRead,
)
async def update_widget_package(
    package_id: UUID,
    payload: WidgetPackageUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> WidgetPackage:
    p = await session.get(WidgetPackage, package_id)
    if p is None:
        raise HTTPException(404, "Пакет не найден")
    role = await _ws_member_role(session, p.workspace_id, user.id)
    is_admin = "users.view" in (user.permissions or [])
    if p.author_user_id != user.id and role != WsRole.MODERATOR and not is_admin:
        raise HTTPException(403, "Менять пакет может только автор или модератор")
    data = payload.model_dump(exclude_unset=True)
    if "html_source" in data and data["html_source"] is not None:
        _require_html_size(data["html_source"])
    for k, v in data.items():
        if v is not None:
            setattr(p, k, v)
    await session.commit()
    await session.refresh(p)
    return p


@router.delete("/widget-packages/{package_id}", status_code=204)
async def delete_widget_package(
    package_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    p = await session.get(WidgetPackage, package_id)
    if p is None:
        raise HTTPException(404, "Пакет не найден")
    role = await _ws_member_role(session, p.workspace_id, user.id)
    is_admin = "users.view" in (user.permissions or [])
    if p.author_user_id != user.id and role != WsRole.MODERATOR and not is_admin:
        raise HTTPException(403, "Удалить пакет может только автор или модератор")
    # Note: widget instances referencing this package via
    # ``chart_options.package_id`` keep the reference; the renderer
    # will show "пакет удалён" rather than leaving a dangling FK.
    await session.delete(p)
    await session.commit()
