"""Per-user agent settings: defaults applied when starting a new run."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentSettings(Base):
    """Defaults a tester sees pre-filled in the New Run modal."""

    __tablename__ = "agent_settings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    default_mode: Mapped[str] = mapped_column(String(20), default="hybrid", nullable=False)
    default_llm_model_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    default_max_steps: Mapped[int] = mapped_column(Integer, default=200, nullable=False)

    # PUCT hyperparameters
    c_puct: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    rollout_depth: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
