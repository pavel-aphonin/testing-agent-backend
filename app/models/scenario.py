"""Scenario model: reusable test-flow templates stored as JSON flowcharts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Scenario(Base):
    """A reusable test scenario with steps stored as a JSON flowchart."""

    __tablename__ = "scenarios"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    # PER-35: optional list of knowledge document UUIDs that scope this
    # scenario's RAG verification to a specific spec. Empty / null means
    # "search the whole workspace corpus" (legacy behaviour). Stored as
    # JSONB so we can query / filter by membership later if needed.
    rag_document_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Workspace this scenario belongs to. Nullable for legacy data.
    workspace_id = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
