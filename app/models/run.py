"""Run + Screen + Edge: the actual exploration data."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.llm_model import LLMModel
    from app.models.user import User


class RunMode(StrEnum):
    AI = "ai"
    MC = "mc"
    HYBRID = "hybrid"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Run(Base):
    """A single exploration session."""

    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Owner
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Target app
    bundle_id: Mapped[str] = mapped_column(String(200), nullable=False)
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), default="ios", nullable=False)

    # Configuration
    mode: Mapped[str] = mapped_column(String(20), default=RunMode.HYBRID.value, nullable=False)
    llm_model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    max_steps: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    c_puct: Mapped[float] = mapped_column(default=2.0, nullable=False)
    rollout_depth: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(20), default=RunStatus.PENDING.value, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stats summary (denormalized for fast list queries)
    stats_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Simulator auto-provisioning (V2 flow). Nullable because the legacy
    # flow (V1) sends a pre-existing device_id and doesn't need these.
    device_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    app_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Path on disk for the explorer output dir (graph.json, screenshots, etc.)
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    screens: Mapped[list["Screen"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    edges: Mapped[list["Edge"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Screen(Base):
    """One unique screen discovered in a run, identified by accessibility hash."""

    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    screen_id_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    visit_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    elements_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["Run"] = relationship(back_populates="screens")


class Edge(Base):
    """A transition: action taken on screen A landed on screen B."""

    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_screen_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_screen_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)  # tap | type | swipe
    action_details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    step_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped["Run"] = relationship(back_populates="edges")
