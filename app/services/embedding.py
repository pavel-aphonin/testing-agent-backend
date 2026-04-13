"""Text embedding client.

Tries to call llama-server's OpenAI-compatible /v1/embeddings endpoint.
If the LLM is unreachable (e.g. no model loaded yet), falls back to a
deterministic hash-based pseudo-embedding so the rest of the RAG pipeline
keeps working end-to-end during local development. The fallback is
clearly marked as fake in logs and in the document's `embedding_model`
column so the operator can re-embed once a real model is available.

The chunker is a deliberate dumb sliding window: split on whitespace,
group ~CHUNK_SIZE tokens, slide by CHUNK_SIZE - CHUNK_OVERLAP. This is
intentionally not the smart "split on sentence boundaries" version
because (a) it's good enough for the demo, (b) it has zero deps, and
(c) it makes test assertions trivial.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from typing import Sequence

import httpx

from app.config import settings
from app.models.knowledge import EMBEDDING_DIM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- chunking ----

CHUNK_SIZE = 300  # words (whitespace-split); Qwen3-Embedding supports 32K tokens
CHUNK_OVERLAP = 30
MAX_CHARS_PER_CHUNK = 8000  # safety limit for Qwen3's 32K token context


def split_into_chunks(text: str) -> list[str]:
    """Sliding-window whitespace tokenizer. Returns non-empty chunks.

    Each chunk is also truncated to MAX_CHARS_PER_CHUNK to stay within
    the embedding model's BPE token context window (512 for bge-small).
    """
    tokens = text.split()
    if not tokens:
        return []
    if len(tokens) <= CHUNK_SIZE:
        chunk = text.strip()[:MAX_CHARS_PER_CHUNK]
        return [chunk] if chunk else []

    chunks: list[str] = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(tokens), step):
        end = start + CHUNK_SIZE
        chunk = " ".join(tokens[start:end]).strip()
        if chunk:
            chunks.append(chunk[:MAX_CHARS_PER_CHUNK])
        if end >= len(tokens):
            break
    return chunks


# ------------------------------------------------------------- embeddings ----


@dataclass
class EmbeddingResult:
    """Embedded text + metadata about how it was embedded."""

    vectors: list[list[float]]
    model_name: str
    dim: int
    is_fake: bool


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _fake_embedding(text: str) -> list[float]:
    """Deterministic pseudo-embedding from SHA-256 of the text.

    This is NOT a real semantic embedding. It exists so the pipeline runs
    end-to-end without llama-server being up. Cosine similarity between
    fake embeddings is meaningless beyond exact-match collisions, so the
    retrieval results in fake mode will essentially be "did you upload
    the same text" — which is exactly what we want for a smoke test.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    floats: list[float] = []
    while len(floats) < EMBEDDING_DIM:
        for b in digest:
            floats.append((b / 255.0) * 2.0 - 1.0)
            if len(floats) >= EMBEDDING_DIM:
                break
        # Re-hash to extend
        digest = hashlib.sha256(digest).digest()
    return _normalize(floats[:EMBEDDING_DIM])


class EmbeddingClient:
    """OpenAI-compatible embeddings client with a hash fallback."""

    def __init__(
        self,
        base_url: str | None = None,
        model_name: str | None = None,
        timeout: float | None = None,
    ) -> None:
        # Prefer the dedicated embedding URL, fall back to the chat URL.
        # In dev on macOS the two are different processes (Gemma for chat
        # on :8080, bge-small for embeddings on :8081); in CI/Linux they
        # can be the same llama-swap behind one host.
        chosen = (
            base_url
            or settings.embedding_base_url
            or settings.llm_base_url
        )
        self.base_url = chosen.rstrip("/")
        self.model_name = model_name or settings.embedding_model_name
        self.timeout = timeout or settings.embedding_request_timeout_sec

    async def _embed_one(
        self,
        client: httpx.AsyncClient,
        text: str,
        *,
        retries: int = 3,
    ) -> list[float]:
        """Embed a single text with retries."""
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"model": self.model_name, "input": text},
                )
                response.raise_for_status()
                data = response.json()
                vec = data["data"][0]["embedding"]
                if len(vec) != EMBEDDING_DIM:
                    raise RuntimeError(
                        f"LLM embedding dim {len(vec)} != expected {EMBEDDING_DIM}"
                    )
                return _normalize(vec)
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
                last_exc = exc
                if attempt < retries:
                    delay = attempt * 2
                    logger.warning(
                        "Embedding attempt %d/%d failed (%s), retrying in %ds…",
                        attempt, retries, exc, delay,
                    )
                    await asyncio.sleep(delay)
        raise RuntimeError(
            f"Сервер ИИ-моделей ({self.base_url}) недоступен. "
            f"Убедитесь, что сервер запущен и попробуйте снова."
        ) from last_exc

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Embed texts one-by-one (bge-small doesn't batch well)."""
        if not texts:
            return EmbeddingResult(
                vectors=[], model_name=self.model_name, dim=EMBEDDING_DIM, is_fake=False
            )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            vectors = []
            for text in texts:
                vec = await self._embed_one(client, text)
                vectors.append(vec)

        return EmbeddingResult(
            vectors=vectors,
            model_name=self.model_name,
            dim=EMBEDDING_DIM,
            is_fake=False,
        )
