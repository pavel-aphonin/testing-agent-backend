"""Defect model: issues the agent detected during a run.

Each defect is tied to a specific run + screen + (optionally) action that
triggered it. The LLM decides whether a failure it observed is a real defect
and assigns a priority, using context like:
  - the element that failed + its expected behavior (from scenario)
  - the RAG-retrieved spec for this screen
  - screenshot before/after the action
  - infra signals (network error, screen didn't load, etc. → filter out)

When integrated with TestOps, high-priority defects are pushed there so QA
can triage the top P0/P1 in one place instead of wading through noise.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


class DefectPriority(StrEnum):
    """Jira-style severity used for ranking defects."""

    P0 = "P0"   # blocker — app crashed / main flow broken
    P1 = "P1"   # critical — feature doesn't work
    P2 = "P2"   # major — works but wrong
    P3 = "P3"   # minor — cosmetic / edge case


class DefectKind(StrEnum):
    """Category of defect. Used for filtering and TestOps routing."""

    FUNCTIONAL = "functional"      # feature doesn't work as specified
    UI = "ui"                      # visual / layout problem
    VALIDATION = "validation"      # field accepts invalid / rejects valid input
    NAVIGATION = "navigation"      # can't reach a screen that should be reachable
    PERFORMANCE = "performance"    # slow / hung
    CRASH = "crash"                # app died
    SPEC_MISMATCH = "spec_mismatch"  # observed behavior contradicts RAG spec
    INFRA_NOISE = "infra_noise"    # network / test data / env problem — NOT a bug


class Defect:
    """Placeholder for type hints — actual definition below as SQLA model."""
    pass


from app.db import Base  # noqa: E402


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

    # LLM-assigned ranking. Priority is what QA filters on in the UI.
    priority: Mapped[str] = mapped_column(
        String(10), default=DefectPriority.P2.value, nullable=False, index=True
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
    llm_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # When TestOps integration is on, this is the external ticket ID once
    # the defect has been pushed there. Empty = not pushed yet.
    external_ticket_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
