"""Upgrade embedding dimension from 384 to 4096 for Qwen3-Embedding-8B.

Revision ID: 20260413_embed
Revises: 20260412_llm_roles
Create Date: 2026-04-13
"""

from alembic import op

revision = "20260413_embed"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

OLD_DIM = 384
NEW_DIM = 4096  # Qwen3-Embedding-8B full output dimension


def upgrade() -> None:
    # 1. Drop the old HNSW index (max 2000 dim — not enough for 4096)
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")

    # 2. Delete all existing chunks & documents (wrong-dim vectors)
    op.execute("DELETE FROM knowledge_chunks")
    op.execute("DELETE FROM knowledge_documents")

    # 3. Change the vector column dimension to 4096
    op.execute(
        f"ALTER TABLE knowledge_chunks "
        f"ALTER COLUMN embedding TYPE vector({NEW_DIM}) "
        f"USING embedding::vector({NEW_DIM})"
    )

    # 4. No ANN index — pgvector HNSW and IVFFlat both cap at 2000 dims.
    #    Sequential scan (exact KNN) is fine for our corpus size (<10K chunks).
    #    If we outgrow this, switch to Matryoshka truncation at 2000 dims.


def downgrade() -> None:
    op.execute("DELETE FROM knowledge_chunks")
    op.execute("DELETE FROM knowledge_documents")
    op.execute(
        f"ALTER TABLE knowledge_chunks "
        f"ALTER COLUMN embedding TYPE vector({OLD_DIM}) "
        f"USING embedding::vector({OLD_DIM})"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
    )
