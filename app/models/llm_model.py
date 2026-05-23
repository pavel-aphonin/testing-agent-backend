"""LLM model registry. Admin uploads + configures, testers pick from active list."""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
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

    # PER-131-lite: thinking-mode passport. ``supports_thinking`` is
    # the master switch — when true, the worker injects
    # ``thinking_activation`` at the start of the system prompt to
    # turn the model's reasoning mode on, and uses
    # ``thinking_extract_regex`` to peel the final answer out of
    # the response. Both columns are model-specific protocol bits;
    # see migration 20260520_per131_thinking for the Gemma 4
    # defaults and how to seed other families.
    supports_thinking: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
    thinking_activation: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_extract_regex: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PER-138: full capabilities adapter. Lets the worker target any
    # LLM backend (llama.cpp, OpenAI-compat, AlphaGen) without code
    # changes — just add a row with the right passport. See
    # migration 20260520_per138 for the column intent.
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="llama_cpp", server_default="llama_cpp",
    )
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    supports_json_schema: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.text("true"),
    )
    supports_multimodal_image: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false"),
    )
    max_context_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=32768, server_default="32768",
    )

    # PER-145 L1: coordinate space the model uses for ``tap_at``.
    # Gemma family emits raw screen points; Qwen2.5/3-VL emits
    # normalized 0–1000; Nemotron emits raw pixels. Worker scales
    # accordingly before calling AXe. See migration
    # 20260522_tap_at_coord_space for the contract.
    tap_at_coord_space: Mapped[str] = mapped_column(
        String(20), nullable=False, default="points", server_default="points",
    )

    # PER-163: minimum image tokens for VLM grounding tasks. When set,
    # the host-services launcher passes ``--image-min-tokens <N>`` to
    # llama-server so the vision encoder uses enough patches to
    # distinguish small UI elements (PIN keypad digits, app-icon
    # grids, calendar cells). Qwen3-VL requires 1024 for reliable
    # grounding per its own startup warning; without it screenshots
    # encode to ~420 tokens and tap_at on a keypad digit lands on a
    # blurry blob. NULL → don't pass the flag (text-only models or
    # models that pick the right budget on their own).
    image_min_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

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
