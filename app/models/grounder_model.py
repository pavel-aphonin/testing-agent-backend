"""Grounder-model registry — dedicated UI-grounder LLMs separate from chat.

See PER-164 for the architectural rationale: dense general-purpose
VLMs fail at canvas keypad grounding, so we plug in a specialised
grounder (UI-TARS family, Molmo, ShowUI, ...) on a second
llama-server port and invoke it specifically for ``tap_at`` with
``element_id=null`` decisions.

This is a separate table from ``llm_models`` on purpose: grounders
are not chat-capable, output a single regex-parseable string
instead of structured JSON, and the contract (prompt template +
response regex + endpoint port) does not map onto the chat-model
passport. One grounder may serve multiple chat-LLMs.
"""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GrounderModel(Base):
    """A locally hosted UI-grounder GGUF model served on its own port."""

    __tablename__ = "grounder_models"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )

    # Identity
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str] = mapped_column(String(50), nullable=False)  # "ui-tars", "molmo", "showui"

    # Files (paths inside the shared volume)
    gguf_path: Mapped[str] = mapped_column(Text, nullable=False)
    mmproj_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, server_default="0")
    quantization: Mapped[str] = mapped_column(String(20), nullable=False)

    # llama-server endpoint. The local launcher reads endpoint_port
    # to bind the second llama-server alongside the chat one
    # (typically 8081 ↔ chat on 8080). endpoint_url overrides
    # everything for remote grounders.
    endpoint_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8081")
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Vision-encoder passport — same semantics as llm_models. See
    # migrations 20260524_per163_image_min_tokens and
    # 20260524_per163_retry_screenshot for the rationale.
    max_context_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="16384")
    image_min_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screenshot_max_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Coordinate space the grounder's output is in. UI-TARS uses
    # normalized 0-1000; Molmo's "point" output is also 0-1000;
    # Ferret-UI uses raw pixels. Worker scales accordingly before
    # calling AXe.
    tap_at_coord_space: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="normalized_1000",
    )

    # Parser contract.
    # ``response_format`` is a short label for logging/audit
    # (``"ui_tars_click_box"``, ``"molmo_point"``, ...).
    # ``response_regex`` is the actual extractor; the worker
    # compiles it once per request and applies to the model's
    # text completion. The regex MUST have exactly two integer
    # capturing groups (x, y) in the grounder's
    # ``tap_at_coord_space``.
    response_format: Mapped[str] = mapped_column(String(50), nullable=False)
    response_regex: Mapped[str] = mapped_column(Text, nullable=False)

    # Prompt template. Worker fills ``{hint}`` (short human-readable
    # target description that chat-LLM produced in its reasoning,
    # e.g. "tap digit 8 on PIN keypad") and attaches the screenshot
    # as a multimodal image_url alongside.
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)

    # Inference defaults — typically greedy for reproducibility.
    default_temperature: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0.0",
    )
    default_top_p: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0",
    )

    # Visibility. One grounder is active per port; the endpoint
    # resolver picks ``is_active=true``. Multiple inactive rows
    # are allowed for benchmarking.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false"),
    )

    # Provenance
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
