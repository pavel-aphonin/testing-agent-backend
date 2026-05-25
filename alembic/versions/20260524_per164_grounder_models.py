"""PER-164 — grounder_models table for dedicated UI-grounder LLMs.

Dense general-purpose VLMs (Qwen3-VL 32B, Gemma 4 26B, Qwen 3.6 27B
— see PER-163 retry #2 comparison comment on PER-163) all fail at
canvas-rendered keypad grounding: they emit one coordinate then
oscillate between the same 1-2 points. ScreenSpot-Pro benchmark
puts Qwen3-VL at ~47%, while purpose-built GUI agents like UI-TARS
score 94+%.

Architecture: chat-LLM (in llm_models) does reasoning and chooses
an action. When the action is ``tap_at`` with ``element_id=null``
(coord-only intent), the worker makes a second LLM call into a
**grounder** running on a separate port — the grounder is fine-
tuned for "screenshot + intent → exact pixel" and returns a
specific text format that the worker parses via a per-row regex.

Grounders are intentionally a separate table — they are not
chat-capable, the contract is different (no JSON-schema, no
multi-turn, single-output regex), and one grounder can serve
multiple chat-LLMs. Coupling them as columns on ``llm_models``
would conflate two responsibilities.

Seed: UI-TARS-1.5-7B with the canonical
``click(start_box='(x,y)')`` output format and a reasonable
``image_min_tokens=1024`` budget (same min as Qwen-VL — UI-TARS
needs at least this for keypad cells; can be tuned upward per
benchmark). ``is_active=true`` since this is the only grounder
we have.

Revision ID: 20260524_per164_grounders
Revises: 20260524_per163_shot_dim
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260524_per164_grounders"
down_revision = "20260524_per163_shot_dim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grounder_models",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Identity
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("family", sa.String(50), nullable=False),

        # Files (same volume convention as llm_models)
        sa.Column("gguf_path", sa.Text(), nullable=False),
        sa.Column("mmproj_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("quantization", sa.String(20), nullable=False),

        # llama-server endpoint. Local launcher reads endpoint_port
        # to bind the second llama-server next to the chat one on
        # :8080. endpoint_url overrides everything for remote
        # grounders (future: ScreenSpot-as-a-service).
        sa.Column("endpoint_port", sa.Integer(), nullable=False, server_default="8081"),
        sa.Column("endpoint_url", sa.Text(), nullable=True),

        # Vision-encoder passport — same semantics as llm_models
        sa.Column("max_context_tokens", sa.Integer(), nullable=False, server_default="16384"),
        sa.Column("image_min_tokens", sa.Integer(), nullable=True),
        sa.Column("screenshot_max_dim", sa.Integer(), nullable=True),

        # Coordinate space the grounder's output is in. UI-TARS uses
        # normalized 0-1000 (same as Qwen-VL); other grounders may
        # emit raw pixels or points. Worker scales accordingly
        # before calling AXe.
        sa.Column("tap_at_coord_space", sa.String(20), nullable=False,
                  server_default="normalized_1000"),

        # Parser contract. response_format is a short label for
        # logging/audit; response_regex is the actual extractor.
        # The regex MUST have exactly two capturing groups: (x, y),
        # both as integer strings, in the grounder's
        # tap_at_coord_space. Worker compiles the regex once per
        # request and applies it to the model's text completion.
        sa.Column("response_format", sa.String(50), nullable=False),
        sa.Column("response_regex", sa.Text(), nullable=False),

        # Prompt template. Worker fills two placeholders:
        # ``{hint}`` — short human-readable target description that
        # chat-LLM produced in its reasoning ("tap digit 8 on PIN
        # keypad"); the screenshot is attached as a multimodal
        # image_url alongside. Different grounders want different
        # system prompts — UI-TARS expects a specific role wrapper,
        # Molmo expects "Point to <X>" phrasing.
        sa.Column("prompt_template", sa.Text(), nullable=False),

        # Inference defaults — grounder typically wants greedy
        # (temperature=0) for reproducibility.
        sa.Column("default_temperature", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("default_top_p", sa.Float(), nullable=False, server_default="1.0"),

        # Visibility. One grounder active at a time per port; the
        # endpoint resolver picks is_active=true. Multiple may
        # coexist as inactive for benchmarking.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        # Provenance
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # Seed UI-TARS-1.5-7B as the first (active) grounder.
    # Files are already on disk at the canonical paths
    # (volumes/llm-models/, mapped to /var/lib/llm-models inside the
    # backend container — same convention as llm_models rows).
    #
    # UI-TARS output format reference:
    #   Action: click(start_box='(312,847)')
    # where (312,847) are coordinates in the **native device pixel
    # space** (iPhone 17 Pro Max → 1170×2532). Empirically verified
    # against gemma-pin-screen.png — model returned (602, 1786) for
    # "digit 8 on PIN keypad" which maps to (51%, 70%) of a
    # 1170×2532 screen → exact center of the '8' button. Sometimes
    # the model adds whitespace inside the tuple, hence \s* in the
    # regex. The prompt template uses the GUI-agent persona from
    # UI-TARS's published system prompt, condensed.
    #
    # Note: tap_at_coord_space=image_pixels means UI-TARS sees the
    # screenshot AFTER it was resized to screenshot_max_dim (we send
    # ~884×1920 from a native 1320×2868 iPhone 17 Pro Max). Its
    # coordinates are in pixels of THAT image, not phone-native
    # pixels. Worker scales back to phone logical points via
    # raw * screen_logical / image_dim — NOT by retina factor like
    # the ``pixels`` space does. Other grounders that emit native
    # phone pixels would use ``pixels``; normalized [0,1000]
    # emitters use ``normalized_1000``.
    op.execute(
        r"""
        INSERT INTO grounder_models (
            name, description, family,
            gguf_path, mmproj_path, size_bytes, quantization,
            endpoint_port,
            max_context_tokens, image_min_tokens, screenshot_max_dim,
            tap_at_coord_space,
            response_format, response_regex, prompt_template,
            default_temperature, default_top_p,
            is_active
        ) VALUES (
            'ui-tars-1.5-7b',
            'UI-TARS-1.5-7B as dedicated grounder for tap_at on canvas UIs (PER-164)',
            'ui-tars',
            '/var/lib/llm-models/UI-TARS-1.5-7B.Q4_K_M.gguf',
            '/var/lib/llm-models/UI-TARS-1.5-7B.mmproj-f16.gguf',
            4400000000,
            'Q4_K_M',
            8081,
            16384, 1024, 1920,
            'image_pixels',
            'ui_tars_click_box',
            'click\(start_box=''\((\d+),\s*(\d+)\)''\)',
            E'You are a GUI agent. Look at the screenshot and locate the target the user describes.\n\nOutput your action in exactly this format, nothing else:\nAction: click(start_box=''(x,y)'')\n\nwhere x and y are integers in [0, 1000] normalized to the screenshot dimensions. Do not add any other text.\n\n## Target\n{hint}',
            0.0, 1.0,
            true
        )
        """
    )


def downgrade() -> None:
    op.drop_table("grounder_models")
