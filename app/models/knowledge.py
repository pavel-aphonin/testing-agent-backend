"""RAG knowledge base: documents the user uploads + their embedding chunks.

A `KnowledgeDocument` is one piece of textual context the user gives the
agent (e.g. a product spec). On upload it gets split into ~500-token
chunks, each chunk is embedded, and the embeddings are stored as
pgvector columns. The agent can then query the knowledge base by
similarity to ground its decisions in user-provided documentation.

The embedding model and dimensionality are stored on the document so we
can reject queries that come from a model with a mismatched dim — and so
we know what to use when re-embedding after a model swap.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Embedding dimensionality. Qwen3-Embedding-8B full output (4096 dim).
# Uses IVFFlat index (no dim limit, unlike HNSW's 2000 max).
EMBEDDING_DIM = 4096


class KnowledgeDocument(Base):
    """One piece of user-uploaded reference material."""

    __tablename__ = "knowledge_documents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(20), default="text", nullable=False
    )

    # Original text. Stored for re-chunking and for showing the user what
    # was uploaded; the agent never reads this directly — it queries chunks.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # How the chunks were embedded. Stored so we can detect drift when the
    # operator switches embedding models.
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(Base):
    """One ~500-token slice of a KnowledgeDocument with its embedding."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=False
    )

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
