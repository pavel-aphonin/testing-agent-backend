"""Scenario shape — admin-extensible palette of node types for the
visual scenario editor (PER-90).

Built-in shapes (the original nine — start, end, action, decision,
wait, screen_check, loop_back, sub_scenario, group) are seeded via
the migration with ``is_builtin=True`` and can be edited but not
deleted. Admins can add new shapes through Workspace → Dictionaries →
Фигуры сценариев; each new shape picks a ``category`` (which
determines runtime semantics) and, for action-category shapes, a
``action_code`` from the ``scenario_actions`` dictionary. The
worker dispatches on category + action_code, so visual variations
of the same underlying action don't need a worker-side change.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ScenarioShape(Base):
    __tablename__ = "scenario_shapes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Stable identifier used inside scenario graphs. Built-in shapes
    # use codes that match the original NodeType literal values
    # (``action``, ``decision``…) for backward compatibility — old
    # scenarios that store ``node.type`` without ``data.shape_code``
    # resolve to the same behaviour as before.
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # One of: start, end, action, decision, wait, screen_check,
    # loop_back, sub_scenario, group. Determines runtime semantics.
    # Free-form for forward-compat — workers reject unknown values.
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    # Render geometry primitive: circle, rect, diamond, pill,
    # trapezoid, hexagon, container.
    geometry: Mapped[str] = mapped_column(String(32), nullable=False)

    # Visual: a hex colour for the shape's accent + optional icon
    # name (an @ant-design/icons component, e.g. ``ThunderboltOutlined``)
    # or a single-character emoji. The frontend looks up the icon by
    # name; falls back to ``QuestionCircleOutlined`` if missing.
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#1677ff")
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Action verb the worker dispatches on for category=action shapes.
    # Other categories ignore this field. Unicode-safe up to 64 chars.
    action_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Editable attribute schema rendered in the node-edit drawer:
    # [{ key, label, type, required?, default?, dict_code?, supports_vars?, multiline? }]
    # Loose typing — frontend handles unknown ``type`` values
    # gracefully (falls back to a string input).
    attributes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # ``True`` → seeded built-in. UI hides the delete button and
    # locks code/category/geometry from editing so the shape stays
    # consistent with the runtime semantics the worker relies on.
    # ``False`` → admin-created custom shape.
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Display order in the picker / palette / help drawer. Smaller
    # comes first; built-ins occupy 0..8.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
