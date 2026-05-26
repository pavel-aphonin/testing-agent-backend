"""/api/runs — list, create, fetch, delete exploration runs.

Permission rules (PER-106 #3, #10):
    - viewing a single run requires either ``users.view`` (admin),
      ownership, or membership in the run's workspace (handled by
      ``_can_access_run`` further down).
    - listing returns admin-sees-all, otherwise own runs ∪ runs in
      any workspace the caller belongs to.
    - creating requires ``runs.create``.
    - cancelling additionally requires ``runs.cancel``.
    - deleting additionally requires ``runs.delete``.
"""

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user, require_permission
from app.config import settings
from app.db import get_async_session
from app.models.device_config import DeviceConfig
from app.models.run import Edge, Run, RunStatus, Screen
from app.models.user import User
from app.schemas.pagination import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    Paginated,
    make_envelope,
    paginate_query,
)
from app.schemas.run import (
    ReplayPathRequest,
    RunCreate,
    RunCreateV2,
    RunRead,
    RunResultRead,
    StartFromScreenRequest,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _has_perm(user: User, perm: str) -> bool:
    return perm in user.permissions


async def _can_access_run(
    user: User, run: Run, session: AsyncSession
) -> bool:
    """Permission gate for per-run endpoints.

    PER-106 #3: a tester can see runs in any workspace they're a member
    of, not just runs they personally created. The previous gate only
    let admins or the run owner through, which made shared dashboards
    useless for teammates.

    Returns True if the caller is:
        * a global admin (``users.view`` permission), or
        * the user who originally created the run, or
        * a member of the run's workspace (when the run has one).
    """
    if _has_perm(user, "users.view"):
        return True
    if run.user_id == user.id:
        return True
    if run.workspace_id is not None:
        from app.auth.users import is_workspace_member
        return await is_workspace_member(session, user.id, run.workspace_id)
    return False


@router.get("", response_model=Paginated[RunRead])
async def list_runs(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
    page: int = Query(
        1,
        ge=1,
        description=(
            "1-indexed page number. Defaults to 1 — calls without "
            "``?page=`` still work, just return the first page."
        ),
    ),
    per_page: int = Query(
        DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
        description=(
            "Page size. Defaults to 50 (matches the default Runs table "
            "render); capped at 200 to keep response payloads bounded."
        ),
    ),
) -> dict:
    """List runs (PER-184: paginated envelope).

    PER-106 #3: visibility is "admin sees all, otherwise you see runs
    you created OR runs in any workspace you belong to". Previously the
    no-filter branch only returned own-runs which silently hid teammates'
    work even though detail endpoints were happy to serve it.

    PER-184: this is the pilot endpoint for ``Paginated[T]``. Workspaces
    with 500+ runs used to ship 30+ MB JSON on every page open because
    we serialised every row regardless of what the UI rendered. The
    response is now an envelope (``items / total / page / per_page /
    has_more``) and the underlying SQL adds OFFSET/LIMIT + a separate
    COUNT(*) so the database does the slicing, not the application.

    If ``workspace_id`` is supplied, narrow to that workspace and verify
    membership up front so we can return a clean 403 instead of an empty
    list.
    """
    q = select(Run).order_by(Run.created_at.desc())

    if workspace_id is not None:
        # Verify membership unless admin
        from app.auth.users import require_workspace_membership
        await require_workspace_membership(session, user, workspace_id)
        q = q.where(Run.workspace_id == workspace_id)
    elif not _has_perm(user, "users.view"):
        # Own runs ∪ runs in any workspace I belong to.
        from app.models.workspace import WorkspaceMember
        ws_ids_result = await session.execute(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == user.id,
            )
        )
        ws_ids = [row[0] for row in ws_ids_result.all()]
        if ws_ids:
            q = q.where(
                (Run.user_id == user.id) | (Run.workspace_id.in_(ws_ids))
            )
        else:
            q = q.where(Run.user_id == user.id)

    rows, total = await paginate_query(session, q, page, per_page)
    return make_envelope(rows, total, page, per_page)


@router.post(
    "",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runs.create"))],
)
async def create_run(
    payload: RunCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Create a new exploration run in `pending` status.

    The actual exploration is started by Block 4 (subprocess runner).
    For now this endpoint just records the request so the UI can list it.
    """
    run = Run(
        user_id=user.id,
        bundle_id=payload.bundle_id,
        device_id=payload.device_id,
        platform=payload.platform,
        mode=payload.mode,
        max_steps=payload.max_steps,
        c_puct=payload.c_puct,
        rollout_depth=payload.rollout_depth,
        status=RunStatus.PENDING.value,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.post(
    "/v2",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("runs.create"))],
)
async def create_run_v2(
    payload: RunCreateV2,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Create a run with automatic simulator provisioning (V2 flow).

    The worker will create a fresh simulator/AVD, install the uploaded
    app, launch it, and tear it down after the run completes.
    """
    # PER-106 #2: validate workspace membership before doing anything.
    # Without this a tester could pass an arbitrary workspace_id and
    # create a run there — the worker would then load that workspace's
    # test_data, leaking credentials into a run the tester shouldn't
    # see.
    from app.auth.users import require_workspace_membership
    await require_workspace_membership(session, user, payload.workspace_id)

    # Verify the app upload exists
    meta_path = Path(settings.app_uploads_dir) / payload.app_file_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(400, "App upload not found. Upload the app first.")
    meta = json.loads(meta_path.read_text())

    # Verify the device config exists and is active
    result = await session.execute(
        select(DeviceConfig).where(DeviceConfig.id == payload.device_config_id)
    )
    device_config = result.scalar_one_or_none()
    if device_config is None:
        raise HTTPException(400, "Device configuration not found")
    if not device_config.is_active:
        raise HTTPException(400, "This device configuration is disabled")

    # PER-106 #5: resolve the LLM model for this run.
    # Priority: explicit payload.llm_model_id → user's AgentSettings
    # default_llm_model_id → None (worker falls back to env var).
    # If a model is chosen, it must exist and be active.
    from app.models.llm_model import LLMModel as _LLMModel
    resolved_model_id = payload.llm_model_id
    if resolved_model_id is None:
        from app.models.agent_settings import AgentSettings
        settings_row = (
            await session.execute(
                select(AgentSettings).where(AgentSettings.user_id == user.id)
            )
        ).scalar_one_or_none()
        if settings_row is not None:
            resolved_model_id = settings_row.default_llm_model_id
    if resolved_model_id is not None:
        model_row = (
            await session.execute(
                select(_LLMModel).where(_LLMModel.id == resolved_model_id)
            )
        ).scalar_one_or_none()
        if model_row is None:
            raise HTTPException(400, "Selected LLM model not found")
        if not model_row.is_active:
            raise HTTPException(
                400, "Selected LLM model is disabled. Pick another."
            )

    run = Run(
        user_id=user.id,
        title=(payload.title or "").strip() or None,
        bundle_id=meta["bundle_id"],
        device_id="__PENDING__",  # populated by worker after sim creation
        platform=meta["platform"],
        mode=payload.mode,
        llm_model_id=resolved_model_id,
        max_steps=payload.max_steps,
        c_puct=payload.c_puct,
        rollout_depth=payload.rollout_depth,
        status=RunStatus.PENDING.value,
        device_type=device_config.device_identifier,
        os_version=device_config.os_identifier,
        app_file_path=meta["app_relative_path"],
        # Empty list = free exploration only. Non-empty = run scenarios first.
        scenario_ids=[str(sid) for sid in payload.scenario_ids] or None,
        pbt_enabled=payload.pbt_enabled,
        workspace_id=payload.workspace_id,
    )
    session.add(run)
    await session.flush()

    # Persist any run-scoped attribute values shipped with the request.
    if payload.attribute_values:
        from app.models.attribute import Attribute, AttributeValue
        from uuid import UUID as _UUID
        for attr_id_str, val in payload.attribute_values.items():
            try:
                attr_id = _UUID(attr_id_str)
            except (ValueError, TypeError):
                continue
            attr = await session.get(Attribute, attr_id)
            if attr is None or attr.applies_to != "run":
                continue
            if attr.is_required and (val is None or val == "" or val == []):
                raise HTTPException(
                    422,
                    f"Атрибут «{attr.name}» обязателен для заполнения",
                )
            session.add(AttributeValue(
                attribute_id=attr_id,
                entity_type="run",
                entity_id=run.id,
                value=val,
            ))

    await session.commit()
    await session.refresh(run)
    return run


@router.get("/{run_id}", response_model=RunRead)
async def get_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")
    return run


@router.get("/{run_id}/results", response_model=RunResultRead)
async def get_run_results(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> RunResultRead:
    """Return the run together with all discovered screens and edges.

    Used by the Results page after exploration finishes. The same row-level
    permission rules as GET /api/runs/{id} apply.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")

    screens_q = await session.execute(
        select(Screen)
        .where(Screen.run_id == run_id)
        .order_by(Screen.first_seen_at.asc())
    )
    edges_q = await session.execute(
        select(Edge).where(Edge.run_id == run_id).order_by(Edge.step_idx.asc())
    )

    return RunResultRead(
        run=RunRead.model_validate(run),
        screens=[s for s in screens_q.scalars().all()],
        edges=[e for e in edges_q.scalars().all()],
    )


@router.get("/{run_id}/edges/{edge_id}/screenshot")
async def get_edge_screenshot(
    run_id: UUID,
    edge_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    side: str = "before",
):
    """Serve the before/after screenshot for an Edge (PER-25 timeline).

    ``side`` is "before" (default) or "after". Returns 404 cleanly
    when the edge has no screenshot for the requested side — older
    runs and MC-mode runs leave both fields null.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")

    from app.models.run import Edge as _Edge
    edge_result = await session.execute(
        select(_Edge).where(_Edge.id == edge_id, _Edge.run_id == run_id)
    )
    edge = edge_result.scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found")
    rel_path = edge.screenshot_after_path if side == "after" else edge.screenshot_before_path
    if not rel_path:
        raise HTTPException(status_code=404, detail="Screenshot not captured")
    file_path = Path(settings.app_uploads_dir) / rel_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing")
    return FileResponse(file_path, media_type="image/png")


@router.get("/{run_id}/diff")
async def get_run_diff(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    against: UUID,
):
    """Diff this run against ``against`` (PER-27).

    Returns lists of screens / edges / defects that were added,
    removed, or persist between the two runs, plus a summary block.
    Both runs must be visible to the caller — check follows the same
    rule as get_run.
    """
    # Visibility check: both runs are loaded with the standard "owner
    # or users.view" gate. Cross-workspace diffs are intentionally
    # *allowed* if the caller can see both runs — same user with two
    # workspaces probably wants to compare them.
    cur = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if cur is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, cur, session):
        raise HTTPException(status_code=403, detail="Not your run")
    base = (await session.execute(select(Run).where(Run.id == against))).scalar_one_or_none()
    if base is None:
        raise HTTPException(status_code=404, detail="Baseline run not found")
    if not await _can_access_run(user, base, session):
        raise HTTPException(status_code=403, detail="Not your baseline run")
    from app.services.run_diff import diff_runs
    return await diff_runs(session, run_id, against)


def _resolve_app_file(payload_app_file_id: str | None, source_run: Run) -> str | None:
    """PER-40 / PER-41: pick the app the new run will install. Falls
    back to the source run's app_file_path when the caller didn't
    upload a new build. Returns the relative path that goes into
    Run.app_file_path."""
    if payload_app_file_id:
        meta_path = Path(settings.app_uploads_dir) / payload_app_file_id / "meta.json"
        if not meta_path.exists():
            raise HTTPException(400, "App upload not found. Upload the app first.")
        meta = json.loads(meta_path.read_text())
        return meta["app_relative_path"]
    return source_run.app_file_path


async def _resolve_device_config(
    session: AsyncSession,
    payload_device_config_id: UUID | None,
    source_run: Run,
) -> DeviceConfig | None:
    """Return the device config to use; reuse source run's config if
    caller didn't override. Validates active state."""
    if payload_device_config_id is not None:
        dc = (
            await session.execute(
                select(DeviceConfig).where(DeviceConfig.id == payload_device_config_id)
            )
        ).scalar_one_or_none()
        if dc is None:
            raise HTTPException(400, "Device configuration not found")
        if not dc.is_active:
            raise HTTPException(400, "This device configuration is disabled")
        return dc
    # Reuse source run's identifiers — there's no FK from Run to
    # DeviceConfig, so we look it up by the (device_identifier,
    # os_identifier) pair.
    if source_run.device_type and source_run.os_version:
        dc = (
            await session.execute(
                select(DeviceConfig).where(
                    DeviceConfig.device_identifier == source_run.device_type,
                    DeviceConfig.os_identifier == source_run.os_version,
                )
            )
        ).scalar_one_or_none()
        return dc
    return None


@router.post("/{run_id}/replay-path", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def replay_path(
    run_id: UUID,
    payload: ReplayPathRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Re-run a recorded edge sequence as a brand-new run (PER-40).

    Validates that the supplied edge_ids form a contiguous chain,
    serialises them into worker-friendly action payloads, and creates
    a fresh Run with replay_actions_json + replay_of pointing back
    at the source. The worker plays back these actions before the
    main exploration loop.

    When ``continue_after_replay`` is False (default), max_steps on
    the new run is set to 0 so the worker stops as soon as the path
    is reproduced — useful for "check if this bug still happens".
    """
    src = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "Source run not found")
    if not await _can_access_run(user, src, session):
        raise HTTPException(403, "Not your run")

    from app.services.path_finder import edges_by_ids, serialize_action
    edges = await edges_by_ids(session, run_id, payload.edge_ids)
    if edges is None:
        raise HTTPException(
            400,
            "edge_ids must belong to this run AND form a connected chain",
        )

    app_path = _resolve_app_file(payload.app_file_id, src)
    if app_path is None:
        raise HTTPException(400, "Source run has no app file and none was supplied")
    dc = await _resolve_device_config(session, payload.device_config_id, src)
    if dc is None:
        raise HTTPException(
            400,
            "Cannot resolve device config — supply device_config_id or "
            "ensure the source run still has a matching DeviceConfig.",
        )
    # Worker resolves bundle_id from the meta.json of the upload, but
    # for legacy compat we keep the source run's bundle_id around.
    bundle_id = src.bundle_id
    if payload.app_file_id:
        meta = json.loads(
            (Path(settings.app_uploads_dir) / payload.app_file_id / "meta.json").read_text()
        )
        bundle_id = meta.get("bundle_id") or bundle_id

    new_run = Run(
        user_id=user.id,
        title=f"Replay run {str(src.id)[:8]}",
        bundle_id=bundle_id,
        device_id="__PENDING__",
        platform=src.platform,
        mode=payload.mode or src.mode,
        max_steps=src.max_steps if payload.continue_after_replay else 0,
        c_puct=src.c_puct,
        rollout_depth=src.rollout_depth,
        status=RunStatus.PENDING.value,
        device_type=dc.device_identifier,
        os_version=dc.os_identifier,
        app_file_path=app_path,
        workspace_id=src.workspace_id,
        replay_of=src.id,
        replay_actions_json=[serialize_action(e) for e in edges],
    )
    session.add(new_run)
    await session.commit()
    await session.refresh(new_run)
    return new_run


@router.post(
    "/{run_id}/start-from-screen",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_from_screen(
    run_id: UUID,
    payload: StartFromScreenRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Run:
    """Start a fresh exploration from a chosen screen (PER-41).

    BFS the source run's edge graph from its root to ``target_screen_hash``,
    serialise the path, and create a new run that replays it then
    continues with ``max_steps`` of free exploration. Mirror of
    replay-path with the path computed server-side instead of given by
    the caller — the UI just needs to pick a node.
    """
    src = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "Source run not found")
    if not await _can_access_run(user, src, session):
        raise HTTPException(403, "Not your run")

    from app.services.path_finder import find_path_to, serialize_action
    path = await find_path_to(session, run_id, payload.target_screen_hash)
    if path is None:
        raise HTTPException(
            400,
            "Target screen is not reachable from the start state in this run's graph.",
        )

    app_path = _resolve_app_file(payload.app_file_id, src)
    if app_path is None:
        raise HTTPException(400, "Source run has no app file and none was supplied")
    dc = await _resolve_device_config(session, payload.device_config_id, src)
    if dc is None:
        raise HTTPException(
            400,
            "Cannot resolve device config — supply device_config_id or "
            "ensure the source run still has a matching DeviceConfig.",
        )
    bundle_id = src.bundle_id
    if payload.app_file_id:
        meta = json.loads(
            (Path(settings.app_uploads_dir) / payload.app_file_id / "meta.json").read_text()
        )
        bundle_id = meta.get("bundle_id") or bundle_id

    new_run = Run(
        user_id=user.id,
        title=f"From screen {payload.target_screen_hash[:8]}",
        bundle_id=bundle_id,
        device_id="__PENDING__",
        platform=src.platform,
        mode=payload.mode or src.mode,
        max_steps=payload.max_steps,
        c_puct=src.c_puct,
        rollout_depth=src.rollout_depth,
        status=RunStatus.PENDING.value,
        device_type=dc.device_identifier,
        os_version=dc.os_identifier,
        app_file_path=app_path,
        workspace_id=src.workspace_id,
        replay_of=src.id,
        replay_actions_json=[serialize_action(e) for e in path],
        started_from_screen_hash=payload.target_screen_hash,
    )
    session.add(new_run)
    await session.commit()
    await session.refresh(new_run)
    return new_run


@router.get("/{run_id}/screens/{screen_hash}/elements")
async def get_screen_elements(
    run_id: UUID,
    screen_hash: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Return the full ``elements_json`` snapshot for one screen (PER-38).

    Lazy-loaded by the ScreenDetailDrawer so the main /results payload
    stays light (a 200-screen run × 50 elements would push 5+ MB into
    every page open). Returns ``{"elements": [...]}`` so the wrapper
    is forward-compatible with future per-screen extras (e.g. layout
    metadata, defect counts).
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")

    screen_result = await session.execute(
        select(Screen).where(
            Screen.run_id == run_id, Screen.screen_id_hash == screen_hash
        )
    )
    screen = screen_result.scalar_one_or_none()
    if screen is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    # ``elements_json`` is JSON column — already a list at the ORM level;
    # wrap defensively in case worker stored it as the legacy single-dict
    # shape on older runs.
    raw = screen.elements_json
    if raw is None:
        elements: list = []
    elif isinstance(raw, list):
        elements = raw
    elif isinstance(raw, dict) and "elements" in raw:
        elements = raw["elements"]
    else:
        elements = []
    return {
        "screen_hash": screen.screen_id_hash,
        "name": screen.name,
        "screenshot_path": screen.screenshot_path,
        "elements": elements,
    }


@router.get("/{run_id}/screens/{screen_hash}/screenshot")
async def get_screen_screenshot(
    run_id: UUID,
    screen_hash: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Serve a screenshot PNG for a specific screen."""
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")

    screen_result = await session.execute(
        select(Screen).where(Screen.run_id == run_id, Screen.screen_id_hash == screen_hash)
    )
    screen = screen_result.scalar_one_or_none()
    if screen is None or not screen.screenshot_path:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    file_path = Path(settings.app_uploads_dir) / screen.screenshot_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing")

    return FileResponse(file_path, media_type="image/png")


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Mark a running run as CANCELLED.

    The worker sees the terminal status on its next heartbeat / event post
    (via the 409 response from /internal/runs/{id}/event) and stops its loop.
    Idempotent — calling on an already-terminal run is a no-op.
    """
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")
    if not _has_perm(user, "runs.cancel"):
        raise HTTPException(status_code=403, detail="Missing runs.cancel permission")

    terminal = {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
    if run.status in terminal:
        return {"status": run.status, "message": "already terminal"}

    from datetime import datetime, timezone
    run.status = RunStatus.CANCELLED.value
    run.finished_at = datetime.now(timezone.utc)
    await session.commit()

    # Broadcast so subscribed UIs see the status flip immediately.
    from app.redis_bus import publish_run_event
    await publish_run_event(
        str(run.id),
        {
            "type": "status_change",
            "new_status": RunStatus.CANCELLED.value,
            "timestamp": run.finished_at.isoformat(),
        },
    )
    return {"status": run.status}


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    """Delete a run. If it's still running, cancel it first so the worker stops."""
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not await _can_access_run(user, run, session):
        raise HTTPException(status_code=403, detail="Not your run")
    if not _has_perm(user, "runs.delete"):
        raise HTTPException(status_code=403, detail="Missing runs.delete permission")

    # If still active, flip to CANCELLED so the worker stops on its next event.
    # We don't wait for the worker to acknowledge — the DELETE still proceeds.
    active = {RunStatus.PENDING.value, RunStatus.RUNNING.value}
    if run.status in active:
        from datetime import datetime, timezone
        from app.redis_bus import publish_run_event
        run.status = RunStatus.CANCELLED.value
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        await publish_run_event(
            str(run.id),
            {
                "type": "status_change",
                "new_status": RunStatus.CANCELLED.value,
                "timestamp": run.finished_at.isoformat(),
            },
        )
        # Refetch to continue deletion
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one()

    await session.delete(run)
    await session.commit()
