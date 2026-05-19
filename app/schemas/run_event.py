"""Pydantic schemas for live run events posted by the worker."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Event types the worker can post. Add new ones here as the protocol grows.
EventType = Literal[
    "status_change",
    "screen_discovered",
    "edge_discovered",
    "log",
    "error",
    "stats_update",
    # PER-18 scenario runner — broadcast-only (not persisted to DB).
    # Frontend renders these as a richer alternative to `log` events,
    # so the user sees scenario progress live. Names match the exact
    # strings worker emits in scenario_runner.py.
    "scenario.started",
    "scenario.step_started",
    "scenario.step_completed",
    "scenario.step_failed",
    "scenario.finished",
    # PER-81 graph traversal — emitted when the runner walks past a
    # node type whose semantics aren't fully wired yet (decision /
    # wait / screen_check / loop_back) or when a back-edge has been
    # crossed too many times. Broadcast-only, not persisted.
    "scenario.node_skipped",
    "scenario.loop_exceeded",
    # PER-85 — emitted when the LLM screen-match decides the live
    # screen does not match the user's description. Broadcast-only.
    "scenario.screen_mismatch",
    # PER-86 — sub-scenario lifecycle. The runner emits these when it
    # enters and leaves a linked scenario triggered by a sub_scenario
    # node, so the timeline can show the nested run as a group.
    "scenario.sub_started",
    "scenario.sub_finished",
    # PER-110 — inner action inside a goal node. Broadcast-only.
    # Lets the timeline render what the LLM is actually doing inside
    # a "Цель" step (one event per inner tap/input/back/swipe).
    "scenario.goal_action",
]


class RunEventIn(BaseModel):
    """One event posted by the worker via /api/internal/runs/{id}/event."""

    type: EventType
    step_idx: int = Field(default=0, ge=0)
    timestamp: datetime | None = None

    # status_change
    new_status: str | None = None

    # screen_discovered
    screen_id_hash: str | None = None
    screen_name: str | None = None
    screenshot_path: str | None = None
    screenshot_b64: str | None = None  # base64 PNG from worker
    is_new: bool | None = None  # True if this is a newly discovered screen

    # edge_discovered
    source_screen_hash: str | None = None
    target_screen_hash: str | None = None
    action_type: str | None = None
    action_details: dict | None = None
    success: bool | None = None
    # Timeline (PER-25): per-edge artefacts. Workers in HYBRID/AI mode
    # send the screenshot they captured before the action and the one
    # right after, plus a one-line LLM rationale. All optional — older
    # workers and MC-mode runs leave these null.
    screenshot_before_b64: str | None = None
    screenshot_after_b64: str | None = None
    llm_reasoning: str | None = None
    # PER-36: RAG verification verdict for the destination screen.
    # Optional dict — see Edge.rag_verdict_json for the shape.
    rag_verdict_json: dict | None = None

    # log / error
    message: str | None = None

    # stats_update
    stats: dict | None = None

    # PER-111: scenario.goal_action fields. Broadcast-only — the UI
    # uses them to render what the LLM picked at each inner step of a
    # goal node (so the timeline shows "input «Введите телефон» via
    # test_data.phone" instead of an empty row). Persist nothing on
    # the backend, just forward to redis_bus.
    inner_step: int | None = None
    action: str | None = None
    element_label: str | None = None
    value_source: str | None = None
    reasoning: str | None = None
    node_id: str | None = None
    scenario_id: str | None = None


class RunClaimResponse(BaseModel):
    """Returned by /api/internal/runs/claim when a worker picks up work."""

    run_id: UUID
    bundle_id: str
    device_id: str
    platform: str
    mode: str
    max_steps: int
    c_puct: float
    rollout_depth: int
    # PER-106 #5: which model the worker should send to llama-swap for
    # this run. Resolved from Run.llm_model_id (which the backend filled
    # in from RunCreateV2.llm_model_id or the user's AgentSettings
    # default). None means "no override" — the worker uses its
    # TA_LLM_MODEL_NAME env var as before.
    llm_model_name: str | None = None
    # V2 auto-provisioning fields (None for legacy V1 runs)
    device_type: str | None = None
    os_version: str | None = None
    app_file_path: str | None = None
    # Test data the agent can use when filling form fields.
    # Keyed by semantic name (e.g. "email", "password", "phone") — the agent
    # picks the right entry based on what the field is asking for. Categorized
    # in the DB but flattened here to simplify the worker.
    test_data: dict[str, str] = {}
    # Expanded scenarios to execute before free exploration (empty = free only).
    # Each scenario has a title + steps; the worker walks them in order and
    # then falls back to free exploration for the remaining max_steps.
    # Step fields: screen_name, action, element_label, value?, expected_result?
    # `value` may contain {{test_data.key}} placeholders that the worker
    # substitutes from `test_data` before sending the action.
    scenarios: list[dict] = []
    # PER-86: library of scenarios pulled in by sub_scenario nodes in
    # the entry-point scenarios. Worker uses these only to resolve
    # sub_scenario calls — they are NOT executed standalone.
    linked_scenarios: list[dict] = []
    # PER-111 v2: enabled action types for this run's workspace,
    # each with code / name / description / arguments_schema. Worker
    # builds the goal-node response_format around this list so the
    # LLM picks ``action`` from a real, editable dictionary instead
    # of a hardcoded enum in code.
    actions: list[dict] = []
    # PER-127: per-workspace screen-stability tuning. Worker uses
    # these to drive _wait_for_screen_stable between dispatching an
    # action and asking the LLM for the next decision. None = use
    # the worker's built-in defaults (5000 ms / 500 ms / built-in
    # keyword list).
    settle_timeout_ms: int = 5000
    settle_poll_ms: int = 500
    loading_indicator_keywords: list[str] = []
    # Property-based testing — when true, the agent's prompt includes
    # validation-probing variants for each field type.
    pbt_enabled: bool = False
    # PER-40 / PER-41: pre-recorded action sequence the worker plays
    # back BEFORE free exploration. Each entry has the shape produced
    # by ``app/services/path_finder.py::serialize_action`` — see worker
    # for how it gets converted to ActionDetail.
    # Empty list = no replay; non-empty + max_steps>0 = replay then
    # explore (PER-41); non-empty + max_steps==0 = replay only (PER-40
    # with continue_after_replay=False).
    replay_actions: list[dict] = []
