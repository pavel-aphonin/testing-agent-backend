"""LLM model registry. Admin uploads + configures, testers pick from active list."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LLMModel(Base):
    """A locally hosted GGUF model served via llama-swap."""

    __tablename__ = "llm_models"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Identity
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str] = mapped_column(String(50), nullable=False)  # "gemma-4", "qwen-3.5"

    # Files (paths inside the shared volume)
    gguf_path: Mapped[str] = mapped_column(Text, nullable=False)
    mmproj_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Capabilities
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    quantization: Mapped[str] = mapped_column(String(20), nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_tool_use: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Inference defaults
    default_temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    default_top_p: Mapped[float] = mapped_column(Float, default=0.9, nullable=False)

    # Measured performance on this hardware (filled by /admin/models/{id}/bench)
    benchmark_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Visibility
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Provenance
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
