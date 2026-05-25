"""/api/internal/chat-model — host-services launcher endpoint.

PER-163: the bash launcher that starts the chat ``llama-server``
on port 8080 needs the *active* model's config (GGUF path, mmproj
path, image_min_tokens, max_context_tokens, ...) so it can pass
the right flags. Hard-coding the model in
``scripts/start-host-services.sh`` violates the "model is data,
not code" principle and means every model swap forces a script
edit AND a script-deploy cycle.

This endpoint reads the single ``is_active`` LLM row that is also
marked as a chat-capable model (vision support, not embeddings /
reranker) and returns the minimal config the launcher needs.

Auth: ``WORKER_TOKEN`` bearer (same as worker uses for claim_next).
The host bash script runs at boot — there is no user context to
authorise against, and the worker token is the closest thing we
have to "trusted host caller".
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.internal_runs import require_worker_token
from app.db import get_async_session
from app.models.llm_model import LLMModel


router = APIRouter(prefix="/api/internal/chat-model", tags=["internal"])


class ChatModelConfig(BaseModel):
    """Minimal launcher contract — everything the bash script needs."""

    name: str
    gguf_path: str
    mmproj_path: str | None = None
    max_context_tokens: int = 32768
    # PER-163: when set, launcher passes --image-min-tokens <N>. NULL
    # means "let llama-server pick its default". Required for Qwen-VL
    # family on grounding tasks (PIN keypads, small icons) where the
    # default picks too few image tokens for reliable pixel-level
    # localisation.
    image_min_tokens: int | None = None
    # PER-138 passport bits the launcher may eventually want too.
    supports_thinking: bool = False
    supports_json_schema: bool = True
    # PER-164 followup: per-model sampling profile. Worker reads
    # these and forwards into the chat-completions payload, so each
    # model gets the family-appropriate temperature/top_p/etc instead
    # of the worker's old hardcoded T=0.2. NULL on top_k/min_p means
    # "let llama-server pick its default" (40 / off).
    default_temperature: float = 0.7
    default_top_p: float = 0.9
    default_top_k: int | None = None
    default_min_p: float | None = None


@router.get(
    "/config",
    response_model=ChatModelConfig,
    dependencies=[Depends(require_worker_token)],
)
async def active_chat_model_config(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ChatModelConfig:
    """Return config for the active chat-capable LLM model.

    "Active and chat-capable" = ``is_active=true`` AND
    ``supports_multimodal_image=true``. If there are several (admin
    forgot to deactivate one), we pick the most recently uploaded
    so the launcher follows the latest experiment. Returns 404
    when there is no such model — the launcher should then fall
    back to its built-in default (and warn loudly so the operator
    knows the launcher is flying blind).
    """
    q = (
        select(LLMModel)
        .where(LLMModel.is_active.is_(True))
        .where(LLMModel.supports_multimodal_image.is_(True))
        .order_by(LLMModel.uploaded_at.desc())
        .limit(1)
    )
    row = (await session.execute(q)).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "no active chat model with supports_multimodal_image=true "
                "found in llm_models"
            ),
        )
    return ChatModelConfig(
        name=row.name,
        gguf_path=row.gguf_path,
        mmproj_path=row.mmproj_path,
        max_context_tokens=int(row.max_context_tokens or 32768),
        image_min_tokens=row.image_min_tokens,
        supports_thinking=bool(row.supports_thinking),
        supports_json_schema=bool(row.supports_json_schema),
        default_temperature=float(row.default_temperature),
        default_top_p=float(row.default_top_p),
        default_top_k=row.default_top_k,
        default_min_p=row.default_min_p,
    )
