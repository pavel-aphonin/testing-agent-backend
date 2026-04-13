"""/api/admin/knowledge — RAG document management.

Admin-only endpoints for uploading reference documents that the agent
can query during exploration runs. Documents are split into ~500-token
chunks, each chunk is embedded via the LLM (or a hash fallback), and
embeddings are stored in pgvector.

The query endpoint is admin-only for the demo so we keep blast radius
contained. Once we wire actual run-time RAG queries from the explorer,
we'll add a worker-token-protected variant under /api/internal/.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import require_admin
from app.db import get_async_session
from app.models.knowledge import EMBEDDING_DIM, KnowledgeChunk, KnowledgeDocument
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentDetail,
    KnowledgeDocumentSummary,
    KnowledgeMatch,
    KnowledgeQuery,
    KnowledgeQueryResponse,
)
from app.services.embedding import EmbeddingClient, split_into_chunks

router = APIRouter(prefix="/api/admin/knowledge", tags=["knowledge"])


# ----------------------------------------------------------------- list ----


@router.get("/documents", response_model=list[KnowledgeDocumentSummary])
async def list_documents(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[KnowledgeDocument]:
    result = await session.execute(
        select(KnowledgeDocument).order_by(KnowledgeDocument.uploaded_at.desc())
    )
    return list(result.scalars().all())


# --------------------------------------------------------------- create ----


@router.post(
    "/documents",
    response_model=KnowledgeDocumentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    payload: KnowledgeDocumentCreate,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> KnowledgeDocument:
    # 1. Chunk the text
    chunks = split_into_chunks(payload.content)
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document content is empty after tokenization",
        )

    # 2. Embed the chunks (retries on failure, raises on total failure)
    embedder = EmbeddingClient()
    try:
        embedding_result = await embedder.embed(chunks)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    if len(embedding_result.vectors) != len(chunks):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Embedding service returned {len(embedding_result.vectors)} "
                f"vectors for {len(chunks)} chunks"
            ),
        )

    # 3. Insert document + chunks in one transaction
    document = KnowledgeDocument(
        title=payload.title,
        source_filename=payload.source_filename,
        source_type=payload.source_type,
        content=payload.content,
        embedding_model=embedding_result.model_name,
        embedding_dim=embedding_result.dim,
        chunk_count=len(chunks),
        uploaded_by_user_id=admin.id,
    )
    session.add(document)
    await session.flush()  # populate document.id

    for idx, (text, vec) in enumerate(zip(chunks, embedding_result.vectors)):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_idx=idx,
                text=text,
                embedding=vec,
            )
        )

    await session.commit()
    await session.refresh(document)
    return document


# -------------------------------------------------------------- file upload ----


@router.post(
    "/documents/upload",
    response_model=KnowledgeDocumentSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document_file(
    file: UploadFile,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> KnowledgeDocument:
    """Upload a document file (PDF, DOCX, XLSX, PPTX, TXT, etc.).

    The server extracts text from the file, chunks it, embeds it, and
    stores it — same as the JSON endpoint but accepts any file format.
    """
    if not file.filename:
        raise HTTPException(400, "Имя файла не указано")

    content_bytes = await file.read()
    if len(content_bytes) > 50_000_000:  # 50 MB
        raise HTTPException(413, "Файл слишком большой (макс. 50 МБ)")

    # Extract text from file
    from app.services.document_parser import extract_text

    try:
        text = extract_text(content_bytes, file.filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    if not text.strip():
        raise HTTPException(400, "Файл не содержит текста")

    title = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename

    # Chunk + embed (same as JSON endpoint)
    chunks = split_into_chunks(text)
    if not chunks:
        raise HTTPException(400, "Не удалось разбить текст на фрагменты")

    embedder = EmbeddingClient()
    try:
        embedding_result = await embedder.embed(chunks)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )

    document = KnowledgeDocument(
        title=title,
        source_filename=file.filename,
        source_type="file",
        content=text,
        embedding_model=embedding_result.model_name,
        embedding_dim=embedding_result.dim,
        chunk_count=len(chunks),
        uploaded_by_user_id=admin.id,
    )
    session.add(document)
    await session.flush()

    for idx, (chunk_text, vec) in enumerate(zip(chunks, embedding_result.vectors)):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_idx=idx,
                text=chunk_text,
                embedding=vec,
            )
        )

    await session.commit()
    await session.refresh(document)
    return document


# ----------------------------------------------------------------- read ----


@router.get(
    "/documents/{document_id}", response_model=KnowledgeDocumentDetail
)
async def get_document(
    document_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> KnowledgeDocument:
    result = await session.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    )
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


# --------------------------------------------------------------- delete ----


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    result = await session.execute(
        delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    await session.commit()


# --------------------------------------------------------- LLM answer ----

_logger = logging.getLogger(__name__)


async def _generate_answer(
    question: str, matches: list[KnowledgeMatch],
) -> str | None:
    """Send retrieved chunks to the LLM and get a concise answer."""
    if not matches:
        return None

    from app.config import settings

    context = "\n\n---\n\n".join(
        f"[{m.document_title}, фрагмент {m.chunk_idx}]\n{m.text}"
        for m in matches[:5]
    )

    prompt = (
        "Ты — ассистент, отвечающий на вопросы по документации. "
        "Используй ТОЛЬКО приведённый контекст. Если ответа нет в контексте — "
        "скажи «В документации нет информации по этому вопросу».\n"
        "Отвечай кратко и по существу, 1-3 предложения. "
        "НЕ используй теги <think>.\n\n"
        f"### Контекст:\n{context}\n\n"
        f"### Вопрос: {question}\n\n"
        "### Ответ:"
    )

    llm_url = settings.llm_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{llm_url}/v1/chat/completions",
                json={
                    "model": "default",
                    "messages": [
                        {"role": "system", "content": "Отвечай кратко и по существу. Не думай вслух."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            # Gemma4 with thinking: answer goes to content, thinking to reasoning_content
            answer = (msg.get("content") or "").strip()
            # If content is empty but reasoning_content exists, extract answer from it
            if not answer and msg.get("reasoning_content"):
                # Model thought but didn't produce answer — likely ran out of tokens
                answer = "Модель обрабатывает запрос, попробуйте ещё раз."
            # Clean up model artifacts
            if answer:
                import re
                # Strip thinking tags
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
                # Strip chat template tags
                answer = re.sub(r"<\|im_end\|>|<\|im_start\|>|<turn\|>|</s>", "", answer)
                answer = answer.strip()
            return answer if answer else None
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return f"Не удалось сгенерировать ответ: {exc}"


# ---------------------------------------------------------------- query ----


@router.post("/query", response_model=KnowledgeQueryResponse)
async def query_knowledge_base(
    payload: KnowledgeQuery,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> KnowledgeQueryResponse:
    """Top-K similar chunks across all documents.

    Uses pgvector's `<=>` cosine distance operator. The HNSW index
    created in the migration makes this an ANN lookup; without it, this
    would still work but degrade to a sequential scan.
    """
    embedder = EmbeddingClient()
    try:
        result = await embedder.embed([payload.query])
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    query_vec = result.vectors[0]

    distance = KnowledgeChunk.embedding.cosine_distance(query_vec).label("distance")
    stmt = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.document_id,
            KnowledgeChunk.chunk_idx,
            KnowledgeChunk.text,
            KnowledgeDocument.title,
            distance,
        )
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .order_by(distance)
        .limit(payload.top_k)
    )
    rows = (await session.execute(stmt)).all()

    matches = [
        KnowledgeMatch(
            chunk_id=row.id,
            document_id=row.document_id,
            chunk_idx=row.chunk_idx,
            text=row.text,
            document_title=row.title,
            distance=float(row.distance),
        )
        for row in rows
    ]

    # Generate LLM answer from retrieved context
    print(f"[RAG] query='{payload.query}', matches={len(matches)}", flush=True)
    answer = await _generate_answer(payload.query, matches)
    print(f"[RAG] answer={repr(answer)[:200]}", flush=True)

    return KnowledgeQueryResponse(
        embedding_model=result.model_name,
        answer=answer,
        matches=matches,
    )
