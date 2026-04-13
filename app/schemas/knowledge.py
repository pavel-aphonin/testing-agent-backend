"""Pydantic schemas for the knowledge base."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentSummary(BaseModel):
    """Lightweight projection used for the admin list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    source_filename: str | None
    source_type: str
    embedding_model: str
    embedding_dim: int
    chunk_count: int
    uploaded_by_user_id: uuid.UUID
    uploaded_at: datetime


class KnowledgeDocumentDetail(KnowledgeDocumentSummary):
    """Detail view: includes the full original text."""

    content: str


class KnowledgeDocumentCreate(BaseModel):
    """JSON-only upload (paste text or markdown into the form)."""

    title: str = Field(min_length=1, max_length=300)
    source_type: str = Field(default="text", pattern="^(text|markdown)$")
    content: str = Field(min_length=1)
    source_filename: str | None = Field(default=None, max_length=300)


class KnowledgeQuery(BaseModel):
    """Search the knowledge base by similarity to a free-form query."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeMatch(BaseModel):
    """One returned chunk + its parent document title + similarity score."""

    document_id: uuid.UUID
    document_title: str
    chunk_id: uuid.UUID
    chunk_idx: int
    text: str
    # Cosine distance from pgvector — lower is more similar (0 == identical).
    distance: float


class KnowledgeQueryResponse(BaseModel):
    embedding_model: str
    answer: str | None = None  # LLM-generated answer based on retrieved chunks
    matches: list[KnowledgeMatch]
