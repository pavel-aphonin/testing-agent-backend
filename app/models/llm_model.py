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
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LLMModel(Base):
    """A locally hosted GGUF model served via llama-swap."""

    __tablename__ = "llm_models"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Identity
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "gui-owl-1.5", "ui-tars-1.5", "qwen3"

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

    # PER-163 retry: maximum dimension (px) of the screenshot sent
    # to this model. Worker scales the simulator screenshot down so
    # that ``max(width, height) <= screenshot_max_dim``, preserving
    # aspect ratio. NULL → legacy behaviour (resize to logical
    # points, e.g. 440x956), which loses detail vital for grounding
    # on small UI controls. Vision-precision models should be set
    # close to the simulator's native pixel resolution (iPhone 17
    # Pro Max native ~1320x2868 — a budget of 1920 keeps full
    # height and lets the encoder see PIN-keypad digits as multi-
    # token shapes rather than single-token blobs.
    screenshot_max_dim: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # Inference defaults. ``default_temperature`` / ``default_top_p``
    # have always been here but were silently ignored by the worker
    # (it hardcoded T=0.2). PER-164 followup wired them through and
    # added ``default_top_k`` / ``default_min_p`` to complete the
    # sampling passport. Each family has a documented sweet spot:
    # Gemma 4 wants (T=0.65, top_p=0.95, top_k=64, min_p=0.05) per
    # Google; Qwen-VL/3.x wants (T=0.7, top_p=0.8, top_k=20, min_p=0)
    # per Alibaba. Wrong T in particular hurts the most — at low T
    # Gemma 4 falls into the documented "low-T attractor" producing
    # impulsive single-step outputs (google-deepmind/gemma#647).
    # Penalty knobs (repeat/presence/frequency) are NOT in the
    # passport on purpose: they corrupt JSON-schema constrained
    # outputs without curing repetition (vllm#40080, ollama#15502).
    default_temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    default_top_p: Mapped[float] = mapped_column(Float, default=0.9, nullable=False)
    default_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_min_p: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Measured performance on this hardware (filled by /admin/models/{id}/bench)
    benchmark_tps: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # PER-193: which agent roles this model is *eligible* to fill.
    # Empty array = legacy / general-purpose (assignable to any role
    # the operator picks — the admin UI just warns "no declared
    # specialisation"). When non-empty, the role picker scopes its
    # choices to models that declare support, so a tiny zero-shot
    # classifier can't accidentally be assigned to PLANNER.
    #
    # Values are strings from ``ModuleRole.value`` to keep the
    # column dialect-portable (Postgres ARRAY of strings vs. an
    # enum array which would force a migration on every role
    # addition). Worker side reads the same enum to validate.
    supported_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)),
        nullable=False,
        default=list,
        server_default="{}",
    )

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
