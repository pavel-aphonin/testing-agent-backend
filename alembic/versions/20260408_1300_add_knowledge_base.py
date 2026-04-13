"""enable pgvector + add knowledge_documents and knowledge_chunks

Revision ID: a3b8c4d5e6f7
Revises: 9f1e7c2a4b80
Create Date: 2026-04-08 13:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "a3b8c4d5e6f7"
down_revision: Union[str, None] = "9f1e7c2a4b80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIM = 384


def upgrade() -> None:
    # 1. Make sure the pgvector extension is available. The Postgres image
    #    is `pgvector/pgvector:pg16` so the binary is already there — we
    #    just need to register it inside this database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. knowledge_documents
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source_filename", sa.String(length=300), nullable=True),
        sa.Column(
            "source_type",
            sa.String(length=20),
            server_default="text",
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column(
            "chunk_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_documents_uploaded_by_user_id"),
        "knowledge_documents",
        ["uploaded_by_user_id"],
    )

    # 3. knowledge_chunks
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_chunks_document_id"),
        "knowledge_chunks",
        ["document_id"],
    )

    # 4. ANN index for cosine similarity. HNSW is the right default for
    #    small-to-medium corpora and supports concurrent inserts well.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_index(
        op.f("ix_knowledge_chunks_document_id"), table_name="knowledge_chunks"
    )
    op.drop_table("knowledge_chunks")
    op.drop_index(
        op.f("ix_knowledge_documents_uploaded_by_user_id"),
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
    # We do NOT drop the vector extension on downgrade — other databases or
    # future migrations may be relying on it.
