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

    # log / error
    message: str | None = None

    # stats_update
    stats: dict | None = None


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
