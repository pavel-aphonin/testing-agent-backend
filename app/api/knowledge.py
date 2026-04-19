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
    workspace_id: UUID | None = None,
) -> list[KnowledgeDocument]:
    q = select(KnowledgeDocument)
    if workspace_id is not None:
        q = q.where(KnowledgeDocument.workspace_id == workspace_id)
    q = q.order_by(KnowledgeDocument.uploaded_at.desc())
    result = await session.execute(q)
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


@router.post(
    "/documents/{document_id}/reembed",
    response_model=KnowledgeDocumentSummary,
)
async def reembed_document(
    document_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> KnowledgeDocument:
    """Re-embed all chunks of a document with the current embedding model.

    Used to rescue documents stuck on `fake-hash-*` (uploaded while the
    real embedding server was down). Re-runs chunking + embedding on the
    original `content` and replaces the existing chunks atomically.
    """
    document = (
        await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    chunks = split_into_chunks(document.content)
    if not chunks:
        raise HTTPException(400, "Document is empty after re-chunking")

    embedder = EmbeddingClient()
    try:
        embedding_result = await embedder.embed(chunks)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    # Replace chunks atomically: delete old, insert new, update parent metadata.
    await session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    )
    for idx, (text, vec) in enumerate(zip(chunks, embedding_result.vectors)):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_idx=idx,
                text=text,
                embedding=vec,
            )
        )
    document.embedding_model = embedding_result.model_name
    document.embedding_dim = embedding_result.dim
    document.chunk_count = len(chunks)
    await session.commit()
    await session.refresh(document)
    return document


# --------------------------------------------------------- LLM answer ----

_logger = logging.getLogger(__name__)


def _clean_model_output(text: str) -> str:
    """Strip thinking tags and chat template artifacts."""
    if not text:
        return text
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|im_end\|>|<\|im_start\|>|<turn\|>|</s>", "", text)
    return text.strip()


async def _generate_answer(
    question: str, matches: list[KnowledgeMatch],
) -> tuple[str | None, list[str]]:
    """Send retrieved chunks to the LLM and get a concise answer with citations.

    Returns (answer, citations). `citations` is a list of exact phrases from the
    source chunks that the answer is grounded in — the UI highlights these.
    Returns ([], []) if the model couldn't produce an answer.
    """
    if not matches:
        return None, []

    from app.config import settings

    context = "\n\n---\n\n".join(
        f"[{m.document_title}, фрагмент {m.chunk_idx}]\n{m.text}"
        for m in matches[:5]
    )

    prompt = (
        "Ты — ассистент, отвечающий на вопросы по документации. "
        "Используй ТОЛЬКО приведённый контекст.\n\n"
        "Верни ОДИН JSON-объект с двумя полями:\n"
        "  - \"answer\": краткий ответ 1-3 предложения на русском\n"
        "  - \"citations\": массив точных цитат из контекста (слово-в-слово, "
        "не перефразируй), на которых основан ответ. 1-3 цитаты по 5-20 слов.\n\n"
        "Если ответа нет в контексте: "
        "{\"answer\": \"В документации нет информации по этому вопросу\", \"citations\": []}\n\n"
        f"### Контекст:\n{context}\n\n"
        f"### Вопрос: {question}\n\n"
        "### JSON:"
    )

    # Prefer the dedicated RAG LLM (Qwen3-8B Instruct).
    llm_url = (settings.rag_llm_base_url or settings.llm_base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{llm_url}/v1/chat/completions",
                json={
                    "model": "default",
                    "messages": [
                        {"role": "system", "content": "Ты отвечаешь только валидным JSON. Не думай вслух."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            raw = _clean_model_output(msg.get("content") or "")

            if not raw and msg.get("reasoning_content"):
                return "Модель обрабатывает запрос, попробуйте ещё раз.", []

            # Parse JSON response
            import json, re
            answer: str | None = None
            citations: list[str] = []
            try:
                parsed = json.loads(raw)
                answer = (parsed.get("answer") or "").strip() or None
                cites = parsed.get("citations") or []
                if isinstance(cites, list):
                    citations = [str(c).strip() for c in cites if str(c).strip()]
            except (json.JSONDecodeError, TypeError):
                # JSON parse failed — try to extract answer string as fallback
                match = re.search(r'"answer"\s*:\s*"([^"]+)"', raw)
                if match:
                    answer = match.group(1).strip()
                else:
                    answer = raw if raw else None

            return answer, citations
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return f"Не удалось сгенерировать ответ: {exc}", []


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

    # Stage 1: vector search. Over-fetch 3× the requested top_k so the reranker
    # has enough candidates to pick from. If reranking is disabled we just take
    # the first top_k in cosine order.
    from app.services.reranker import RerankerClient
    reranker = RerankerClient()
    retrieval_k = payload.top_k * 3 if reranker.enabled else payload.top_k

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
        .limit(retrieval_k)
    )
    rows = (await session.execute(stmt)).all()

    candidates = [
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

    # Stage 2: rerank the candidates. Returns None if reranker is down —
    # in that case we keep the vector-search order.
    rerank_results = await reranker.rerank(
        payload.query,
        [c.text for c in candidates],
        top_n=payload.top_k,
    )
    if rerank_results is not None:
        # Reorder by reranker score; preserve distance for UI display
        matches = [candidates[r.index] for r in rerank_results]
        print(f"[RAG] reranked {len(candidates)} → top {len(matches)}", flush=True)
    else:
        matches = candidates[: payload.top_k]

    # Generate LLM answer from retrieved context, with citations for highlighting
    print(f"[RAG] query='{payload.query}', matches={len(matches)}", flush=True)
    answer, citations = await _generate_answer(payload.query, matches)
    print(f"[RAG] answer={repr(answer)[:150]} citations={len(citations)}", flush=True)

    return KnowledgeQueryResponse(
        embedding_model=result.model_name,
        answer=answer,
        citations=citations,
        matches=matches,
    )
