"""Pydantic schemas for the Run resource."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.run import RunMode


class RunCreate(BaseModel):
    """V1 run creation — requires pre-existing device + manually installed app."""

    bundle_id: str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(..., min_length=1, max_length=200)
    platform: str = Field(default="ios", max_length=20)
    mode: str = Field(default=RunMode.HYBRID.value)
    max_steps: int = Field(default=200, ge=1, le=10000)
    c_puct: float = Field(default=2.0, ge=0.0, le=10.0)
    rollout_depth: int = Field(default=5, ge=0, le=100)


class RunCreateV2(BaseModel):
    """V2 run creation — worker auto-provisions simulator/emulator."""

    # Optional user-supplied name for the run, e.g. "Smoke 2026-04-15".
    # Empty/None = backend will not store anything; UI renders "Запуск от
    # {created_at}" as a fallback.
    title: str | None = Field(default=None, max_length=200)
    # Reference to the uploaded .app.zip / .ipa / .apk (from POST /api/uploads/app)
    app_file_id: str = Field(..., min_length=1, max_length=200)
    # Device config ID from the admin-curated list (GET /api/devices)
    device_config_id: UUID
    mode: str = Field(default=RunMode.HYBRID.value)
    max_steps: int = Field(default=200, ge=1, le=10000)
    c_puct: float = Field(default=2.0, ge=0.0, le=10.0)
    rollout_depth: int = Field(default=5, ge=0, le=100)
    # Optional scenarios to run before free exploration (empty = free only).
    # The agent executes scenarios sequentially, then continues with free
    # exploration for the remaining max_steps.
    scenario_ids: list[UUID] = Field(default_factory=list)
    # Property-based testing: probe form validation with edge-case values.
    pbt_enabled: bool = False
    # Workspace this run belongs to. The frontend sends the active
    # workspace from the WorkspaceSwitcher.
    workspace_id: UUID | None = None
    # Run-scoped attribute values: { attribute_id: value }. Backend
    # persists each as an AttributeValue row with entity_type="run"
    # and entity_id=new_run.id.
    attribute_values: dict[str, object] | None = None


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str | None = None
    bundle_id: str
    device_id: str
    platform: str
    mode: str
    status: str
    max_steps: int
    c_puct: float
    rollout_depth: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    stats_json: dict | None = None
    # V2 fields
    device_type: str | None = None
    os_version: str | None = None
    app_file_path: str | None = None


# ── Simulator config schemas (for admin device management) ──

class SimulatorRuntime(BaseModel):
    name: str
    identifier: str
    platform: str

class SimulatorDeviceType(BaseModel):
    name: str
    identifier: str
    platform: str

class SimulatorConfigReport(BaseModel):
    runtimes: list[SimulatorRuntime]
    device_types: list[SimulatorDeviceType]


# ── Device config schemas (admin-curated) ──

class DeviceConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: str
    device_type: str
    device_identifier: str
    os_version: str
    os_identifier: str
    is_active: bool
    created_at: datetime

class DeviceConfigCreate(BaseModel):
    platform: str = Field(..., pattern="^(ios|android)$")
    device_type: str = Field(..., min_length=1, max_length=200)
    device_identifier: str = Field(..., min_length=1, max_length=300)
    os_version: str = Field(..., min_length=1, max_length=50)
    os_identifier: str = Field(..., min_length=1, max_length=300)

class DeviceConfigUpdate(BaseModel):
    is_active: bool | None = None


class ScreenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    screen_id_hash: str
    name: str
    visit_count: int
    screenshot_path: str | None
    first_seen_at: datetime


class EdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_screen_hash: str
    target_screen_hash: str
    action_type: str
    action_details_json: dict | None
    success: bool
    step_idx: int
    created_at: datetime


class RunResultRead(BaseModel):
    """Bundled run + its discovered screens and edges for the Results page."""

    run: RunRead
    screens: list[ScreenRead]
    edges: list[EdgeRead]
