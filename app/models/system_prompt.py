"""System prompts — admin-editable LLM prompts kept in the DB (PER-111).

The original design hard-coded every LLM prompt as a Python string. That
worked while there was one stack with one operator, but the moment we
sell the platform to anyone else (Альфа-Банк first, then anyone), the
admin needs a way to tweak phrasing without a deploy AND a safety net
to undo their changes — so the prompts move to the database.

The table holds one row per logical prompt slot (``goal_decide.system``,
``goal_decide.user``, later ``free_exploration.system`` and so on). Each
row keeps both the current ``content`` (admin-mutable) and the original
``default_content`` baked into the migration; the admin UI exposes a
"Reset to default" button that copies the latter back into the former.

The ``placeholders`` column documents the {{var}}-style placeholders the
worker substitutes at render time. The API uses it to validate that an
admin edit didn't accidentally drop a placeholder the worker depends on.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SystemPrompt(Base):
    __tablename__ = "system_prompts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Stable identifier used by the worker / other backend code to fetch
    # the prompt — e.g. ``goal_decide.system``. Unique because we look
    # up by code rather than by id from the runtime side.
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Admin-facing display name + one-sentence description. Render in
    # the prompts list in the admin UI so the operator knows what they
    # are about to edit before opening the textarea.
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The current text. Mutable through the admin API. Substituted with
    # placeholder values at render time by the worker.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # The text seeded by the migration. ``POST /reset`` copies this
    # back into ``content`` so the admin always has a way out of a bad
    # edit. Kept up-to-date by future migrations when we ship improved
    # defaults — those will overwrite ``default_content`` and also
    # ``content`` for rows where ``content == old_default_content``.
    default_content: Mapped[str] = mapped_column(Text, nullable=False)

    # Required {{placeholder}} names the worker substitutes when
    # rendering this prompt — e.g. ``["goal", "step_idx", "max_steps",
    # "elements_block", "test_data_block", "history_block",
    # "value_sources_list"]``. The API rejects edits where any required
    # placeholder is missing from ``content`` so the worker can't end
    # up rendering a half-substituted prompt.
    placeholders: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # ``True`` → seeded built-in. The UI hides the delete button and
    # locks ``code`` from editing; the operator can change ``content``
    # and the cosmetic fields, but the slot itself stays.
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
