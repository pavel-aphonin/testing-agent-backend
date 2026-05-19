"""Defect model: issues the agent detected during a run.

Each defect is tied to a specific run + screen + (optionally) action that
triggered it. The LLM decides whether a failure it observed is a real defect,
then assigns one row from each of two reference tables:

* ``ref_defect_priorities`` — how urgent the fix is (Urgent / High / Medium / Low).
* ``ref_defect_severities`` — how severe the bug itself is (Blocker / Critical / …).

Both reference tables are admin-editable (see PER-120). The legacy
``DefectPriority`` StrEnum (P0/P1/P2/P3) is gone — callers that used to
import it should now look up the priority row by ``code`` instead.

When integrated with TestOps, high-priority defects are pushed there so QA
can triage the top ones in one place instead of wading through noise.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


from app.db import Base


class DefectKind(StrEnum):
    """Category of defect. Used for filtering and TestOps routing.

    Kind is a fixed taxonomy — unlike priority/severity, it doesn't need
    a reference table because we use it as a routing discriminator, not
    as a user-tunable scale. Add new values here when a new defect
    routing path is required.
    """

    FUNCTIONAL = "functional"      # feature doesn't work as specified
    UI = "ui"                      # visual / layout problem
    VALIDATION = "validation"      # field accepts invalid / rejects valid input
    NAVIGATION = "navigation"      # can't reach a screen that should be reachable
    PERFORMANCE = "performance"    # slow / hung
    CRASH = "crash"                # app died
    SPEC_MISMATCH = "spec_mismatch"  # observed behavior contradicts RAG spec
    INFRA_NOISE = "infra_noise"    # network / test data / env problem — NOT a bug


class DefectPriorityRef(Base):
    """Reference: how urgent the fix is. Admin-editable via PER-120 UI."""

    __tablename__ = "ref_defect_priorities"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#8c8c8c")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DefectSeverityRef(Base):
    """Reference: how severe the defect itself is. Admin-editable."""

    __tablename__ = "ref_defect_severities"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#8c8c8c")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DefectModel(Base):
    """One agent-detected defect with full context for triage."""

    __tablename__ = "defects"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # When during the run this was flagged (1-indexed to match step_idx on Edge).
    step_idx: Mapped[int | None] = mapped_column(nullable=True)

    # Which screen triggered it. Hash matches Screen.screen_id_hash.
    screen_id_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    screen_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # PER-120: priority and severity are now FK references. Sort order
    # for triage UI comes from the referenced row's `sort_order`.
    priority_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ref_defect_priorities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    severity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ref_defect_severities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    priority: Mapped[DefectPriorityRef] = relationship(
        "DefectPriorityRef", foreign_keys=[priority_id], lazy="joined"
    )
    severity: Mapped[DefectSeverityRef] = relationship(
        "DefectSeverityRef", foreign_keys=[severity_id], lazy="joined"
    )

    kind: Mapped[str] = mapped_column(
        String(20), default=DefectKind.FUNCTIONAL.value, nullable=False, index=True
    )

    # Short one-line title. The LLM writes this. Example:
    # "Поле Email принимает строку без @"
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Free-form description with reproduction steps, expected vs actual, etc.
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Screenshot at the moment the defect was detected (path in worker_runs).
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The raw LLM analysis — helps us understand WHY the model flagged this.
    # Useful for debugging false positives and tuning the defect-detection
    # prompt. Kept as JSON so we can add fields without migrations.
    llm_analysis_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # When TestOps integration is on, this is the external ticket ID once
    # the defect has been pushed there. Empty = not pushed yet.
    external_ticket_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
