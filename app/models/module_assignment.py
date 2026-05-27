"""PER-193 (PER-175 Phase 1B): per-role LLM assignment.

The 12-module agent pipeline needs the backend to know which catalog
``LLMModel`` is currently filling each of the 11 roles (Planner /
Grounder / Safety / etc.). Two pieces of state together:

* ``LLMModel.supported_roles`` (column added in this migration) —
  declarative «this model is *eligible* for these roles», so the UI
  can scope the picker per role (a tiny zero-shot classifier suits
  ``CONTEXT_IDENTIFIER`` but not ``PLANNER``).

* ``module_assignments`` (this module) — current «role → model»
  mapping. One row per role; ``llm_model_id`` may be NULL until an
  operator assigns one through the admin UI.

Why a separate table instead of a column on ``LLMModel``:
    * 1-model-per-role is the wrong shape — the same physical model
      can multiplex roles (GUI-Owl-1.5-4B serves Planner / Reflection
      / Reward Critic at once). Storing the assignment on LLMModel
      forces an N:M side-table anyway, and we already need the
      ``assigned_at`` / ``assigned_by_user_id`` metadata for audit.
    * Looking up «who's currently the Planner» is one SELECT on a
      11-row table — trivial cost, single source of truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ModuleRole(str, Enum):
    """Eleven specialised roles in the 12-module pipeline.

    Stable string identifiers — values are also used as the database
    enum strings, frontend i18n keys, and prometheus labels. Renaming
    a value is a breaking change; additions are append-only.

    ``GROUNDING_VERIFIER`` may stay unassigned (``llm_model_id``
    NULL) in the typical setup — it's currently implemented as
    logprobs calibration on the Planner's output, not a separate
    model. The slot exists so a future verifier model can be plugged
    in without a schema change.
    """

    SCREEN_PARSER = "SCREEN_PARSER"
    DYNAMIC_PERCEIVER = "DYNAMIC_PERCEIVER"
    CONTEXT_IDENTIFIER = "CONTEXT_IDENTIFIER"
    PLANNER = "PLANNER"
    GROUNDER = "GROUNDER"
    GROUNDING_VERIFIER = "GROUNDING_VERIFIER"
    MEMORY = "MEMORY"
    REFLECTION = "REFLECTION"
    SAFETY_GUARD = "SAFETY_GUARD"
    REWARD_CRITIC = "REWARD_CRITIC"
    AMBIGUITY_RESOLVER = "AMBIGUITY_RESOLVER"


# Ordered list used by the migration seed + the admin UI's "show me
# all 11 rows even when some aren't assigned" guarantee. Keep in
# sync with the enum above.
ALL_MODULE_ROLES: tuple[ModuleRole, ...] = tuple(ModuleRole)


class ModuleAssignment(Base):
    """Which catalog ``LLMModel`` currently fills each agent role.

    Invariant: exactly one row per ``role``. The migration seeds 11
    rows (one per ``ModuleRole``) with ``llm_model_id=NULL``; the
    admin UI flips the FK as operators pick. ``llm_model_id`` stays
    nullable so an operator can deliberately unassign a role
    (e.g. ``GROUNDING_VERIFIER`` when the logprobs-only mode is
    sufficient) without deleting the row.
    """

    __tablename__ = "module_assignments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
    )

    # The role this row controls. Single-row-per-role is enforced
    # below — UNIQUE on role means operators can't accidentally
    # create a second "PLANNER" assignment via the API.
    role: Mapped[str] = mapped_column(String(50), nullable=False)

    # FK to the catalog model currently assigned to this role.
    # NULL → no model picked yet (UI shows "—" / "Не назначено").
    # ``ondelete="SET NULL"`` so deleting a model from the catalog
    # doesn't cascade-delete the assignment row — instead it
    # surfaces a clear «role lost its model» state in the UI.
    llm_model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    llm_model = relationship("LLMModel", lazy="joined")

    # Audit metadata. ``assigned_by_user_id`` is NULL for seeded
    # rows (no user picked them — the migration did).
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("role", name="uq_module_assignments_role"),
    )
