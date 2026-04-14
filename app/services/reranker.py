"""Qwen3-Reranker client for two-stage RAG retrieval.

Pipeline:
    1. Embedding vector search (coarse): top-K candidates by cosine similarity
       over the full corpus. Fast but imprecise — just checks "is this chunk
       in the same semantic neighborhood".
    2. Reranker (fine): re-score the top-K candidates against the actual query
       using a cross-encoder that reads both at once. Slower (O(K)) but much
       more accurate — actually answers "does this chunk answer the question".

The reranker server is an optional third llama-server on :8084 started with
`--reranking --pooling rank`. If it's unreachable we silently skip reranking
and return the vector-search order — RAG still works, just with worse precision.

See testing-agent-infra/scripts/start-host-services.sh for the server config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RerankResult:
    """Scored index into the input documents list, highest score first."""

    index: int
    score: float


class RerankerClient:
    """llama-server /v1/rerank client with graceful degradation."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.reranker_base_url).rstrip("/")
        self.timeout = timeout or 15.0

    @property
    def enabled(self) -> bool:
        """False when no reranker URL is configured — callers should skip."""
        return bool(self.base_url)

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult] | None:
        """Score documents against query, return sorted by relevance.

        Returns None on transport failure so the caller can fall back to the
        vector-search order. Never raises — reranking is always best-effort.
        """
        if not self.enabled or not documents:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/rerank",
                    json={
                        "model": "reranker",
                        "query": query,
                        "documents": list(documents),
                        "top_n": top_n or len(documents),
                    },
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                # llama-server returns [{index, relevance_score}, ...]
                # already sorted by score desc.
                return [
                    RerankResult(
                        index=int(r["index"]),
                        score=float(r.get("relevance_score", 0.0)),
                    )
                    for r in results
                ]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning(
                "Reranker at %s unavailable (%s) — using vector-search order",
                self.base_url, exc,
            )
            return None
