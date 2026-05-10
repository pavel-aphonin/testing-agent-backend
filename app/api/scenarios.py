"""/api/scenarios — CRUD for reusable test scenarios.

Permission rules:
    - any authenticated user can list active scenarios and get one by id
    - tester/admin can create, update, and delete scenarios
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import ValidationError

from app.auth.users import current_active_user, require_tester
from app.db import get_async_session
from app.models.scenario import Scenario
from app.models.user import User
from app.schemas.scenario import ScenarioCreate, ScenarioRead, ScenarioUpdate
from app.schemas.scenario_graph import normalize as _normalize_graph
from app.services.scenario_validator import find_unresolved_placeholders


def normalize_graph(steps_json: dict | None) -> dict:
    """Wrapper that converts pydantic ValidationError into HTTP 400.

    The normalizer itself raises a regular ``ValidationError`` for
    malformed v2 input (e.g. multiple start nodes). FastAPI does not
    auto-translate exceptions raised inside our own code, so we have to
    convert here to keep responses informative.

    ``exc.errors()`` includes a ``ctx.error`` field which holds the
    original ``ValueError`` instance — that's not JSON-serializable, so
    we project the errors down to the safe fields the UI cares about.
    """
    try:
        return _normalize_graph(steps_json)
    except ValidationError as exc:
        clean_errors = [
            {
                "type": e.get("type"),
                "loc": list(e.get("loc", [])),
                "msg": e.get("msg"),
            }
            for e in exc.errors()
        ]
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_scenario_graph",
                "message": "Граф сценария невалиден",
                "errors": clean_errors,
            },
        ) from exc

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


async def _check_placeholders(
    session: AsyncSession,
    workspace_id: UUID | None,
    steps_json: dict | None,
) -> None:
    """Reject the request if any ``{{test_data.X}}`` placeholder in
    the steps doesn't resolve in the target workspace's TestData.
    Raises HTTPException(400) with a list of missing keys so the UI
    can highlight them. See PER-21."""
    # PER-80: payload may be either the legacy ``{"steps": [...]}`` or
    # the new graph ``{"nodes": [...]}``. Build a list of step dicts
    # the validator can scan, regardless of shape.
    raw = steps_json or {}
    if isinstance(raw.get("nodes"), list):
        steps = [
            n.get("data") or {}
            for n in raw["nodes"]
            if isinstance(n, dict) and n.get("type") == "action"
        ]
    else:
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else None
    missing = await find_unresolved_placeholders(session, workspace_id, steps)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unresolved_placeholders",
                "message": (
                    "В сценарии используются ключи test_data, которых нет "
                    "в этом пространстве: " + ", ".join(missing)
                ),
                "missing_keys": missing,
            },
        )


@router.get("", response_model=list[ScenarioRead])
async def list_scenarios(
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    workspace_id: UUID | None = None,
    only_active: bool = False,
) -> list[Scenario]:
    """List scenarios.

    By default returns BOTH active and inactive scenarios — the admin UI
    needs to see everything in order to flip the activation toggle. The
    worker (and any other consumer that wants only runnable scenarios)
    passes ``only_active=true``. Filtered by workspace if provided.
    """
    q = select(Scenario)
    if only_active:
        q = q.where(Scenario.is_active.is_(True))
    if workspace_id is not None:
        q = q.where(Scenario.workspace_id == workspace_id)
    q = q.order_by(Scenario.created_at.desc())
    result = await session.execute(q)
    items = list(result.scalars().all())
    # PER-80: belt-and-suspenders. The migration converts every row
    # at upgrade time, but if anything slipped through (e.g. a row
    # written by an older deploy in flight) we still want clients to
    # only ever see graph-shaped payloads.
    for s in items:
        s.steps_json = normalize_graph(s.steps_json)
    return items


@router.post(
    "",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tester)],
)
async def create_scenario(
    payload: ScenarioCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    # PER-80: clients (and the legacy frontend) may still POST flat v1
    # ``{"steps": [...]}`` payloads. Always normalize to the graph
    # shape before persisting so the rest of the system never sees v1.
    graph_steps = normalize_graph(payload.steps_json)
    await _check_placeholders(session, payload.workspace_id, graph_steps)
    scenario = Scenario(
        title=payload.title,
        description=payload.description,
        steps_json=graph_steps,
        created_by_user_id=user.id,
        workspace_id=payload.workspace_id,
        # PER-68: new scenarios are always inactive. Explicit activation
        # via PATCH is required so a freshly-created stub doesn't get
        # picked up by an in-flight run before the user has reviewed it.
        is_active=False,
        # PER-35: link to specific RAG documents so the worker scopes
        # spec-verification to them (None/empty = whole workspace corpus).
        rag_document_ids=(
            [str(d) for d in payload.rag_document_ids]
            if payload.rag_document_ids
            else None
        ),
    )
    session.add(scenario)
    await session.commit()
    await session.refresh(scenario)
    return scenario


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(
    scenario_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    # PER-80: same belt-and-suspenders as the list endpoint.
    scenario.steps_json = normalize_graph(scenario.steps_json)
    return scenario


@router.patch(
    "/{scenario_id}",
    response_model=ScenarioRead,
    dependencies=[Depends(require_tester)],
)
async def update_scenario(
    scenario_id: UUID,
    payload: ScenarioUpdate,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Scenario:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    patch = payload.model_dump(exclude_unset=True)
    # PER-80: incoming steps_json may be v1 or v2 — normalize before
    # storage and validation so the rest of the system never sees v1.
    if "steps_json" in patch:
        patch["steps_json"] = normalize_graph(patch["steps_json"])
    # If the patch touches steps_json (or workspace_id) — re-validate
    # placeholders against the *new* state. Use the fresh value where
    # provided, fall back to the existing one otherwise.
    if "steps_json" in patch or "workspace_id" in patch:
        await _check_placeholders(
            session,
            patch.get("workspace_id", scenario.workspace_id),
            patch.get("steps_json", scenario.steps_json),
        )
    # PER-68: deactivating a scenario that's referenced by a pending
    # or running run would leave the worker without a script mid-flight.
    # Refuse with 409 + the offending run IDs so the user can cancel
    # them first. ``scenario_ids`` is a JSON list[str], so we filter
    # in Python — the table is small enough that this is fine.
    if "is_active" in patch and scenario.is_active and patch["is_active"] is False:
        from app.models.run import Run, RunStatus
        rows = (
            await session.execute(
                select(Run.id, Run.title, Run.scenario_ids).where(
                    Run.scenario_ids.isnot(None),
                    Run.status.in_(
                        [RunStatus.PENDING.value, RunStatus.RUNNING.value]
                    ),
                )
            )
        ).all()
        sid_str = str(scenario_id)
        blocking = [
            (str(rid), title)
            for rid, title, sids in rows
            if sids and sid_str in sids
        ]
        if blocking:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "scenario_in_use",
                    "message": (
                        "Сценарий используется в активных прогонах: "
                        + ", ".join(t or rid[:8] for rid, t in blocking)
                        + ". Отмените их прежде чем деактивировать сценарий."
                    ),
                    "run_ids": [rid for rid, _ in blocking],
                },
            )
    for field, value in patch.items():
        setattr(scenario, field, value)

    await session.commit()
    await session.refresh(scenario)
    return scenario


@router.delete(
    "/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_tester)],
)
async def delete_scenario(
    scenario_id: UUID,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        select(Scenario).where(Scenario.id == scenario_id)
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    # PER-68: don't let users delete an active scenario without first
    # deactivating it. Deletion of an active scenario could orphan
    # in-flight or queued runs that point to it.
    if scenario.is_active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "scenario_active",
                "message": (
                    "Сначала отключите сценарий — активный сценарий "
                    "удалить нельзя, чтобы не оборвать запуски."
                ),
            },
        )
    await session.delete(scenario)
    await session.commit()
