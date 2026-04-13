"""HuggingFace model browser — admin-only endpoints.

Three HTTP endpoints (search, list files, start download) plus the
WebSocket endpoint in ``app/api/download_ws.py``. All require the
admin role; testers and viewers never interact with this router.

The actual downloading happens in ``app/services/hf_downloader.py``;
this router is the thin HTTP layer over it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError, RepositoryNotFoundError

from app.auth.users import require_admin
from app.models.user import User
from app.schemas.hf_models import (
    HfDownloadRequest,
    HfDownloadStarted,
    HfFile,
    HfRepoSummary,
)
from app.services.hf_downloader import spawn_download

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/models/hf", tags=["admin-hf"])


@router.get("/search", response_model=list[HfRepoSummary])
async def search_hf(
    _admin: Annotated[User, Depends(require_admin)],
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[HfRepoSummary]:
    """Full-text search over HF repo names, pre-filtered to GGUF repos.

    The ``filter="gguf"`` tag restricts results to repos that HF has
    indexed as containing GGUF files, which hides the original
    safetensors/pytorch checkpoints that llama.cpp can't load.
    """
    api = HfApi()
    try:
        models = list(api.list_models(
            search=q, filter="gguf", limit=limit, sort="downloads",
        ))
    except HfHubHTTPError as exc:
        logger.warning("HF search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="HuggingFace search failed",
        ) from exc

    out: list[HfRepoSummary] = []
    for m in models:
        out.append(
            HfRepoSummary(
                repo_id=m.modelId,
                downloads=getattr(m, "downloads", None),
                likes=getattr(m, "likes", None),
                last_modified=getattr(m, "last_modified", None),
                library_name=getattr(m, "library_name", None),
                tags=list(getattr(m, "tags", []) or []),
            )
        )
    return out


@router.get("/repo/{owner}/{name}/files", response_model=list[HfFile])
async def list_repo_files_endpoint(
    owner: str,
    name: str,
    _admin: Annotated[User, Depends(require_admin)],
) -> list[HfFile]:
    """Return all ``.gguf`` files in a repo with sizes.

    Uses ``repo_info(files_metadata=True)`` so we get sizes in one call.
    Non-GGUF entries (README.md, .gitattributes, etc.) are filtered
    out server-side to keep the UI focused.
    """
    repo_id = f"{owner}/{name}"
    api = HfApi()
    try:
        info = api.repo_info(repo_id, files_metadata=True)
    except RepositoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"HF repo not found: {repo_id}",
        ) from exc
    except HfHubHTTPError as exc:
        logger.warning("HF repo_info failed for %s: %s", repo_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="HuggingFace API error",
        ) from exc

    files: list[HfFile] = []
    for sibling in info.siblings or []:
        if sibling.rfilename.lower().endswith(".gguf"):
            files.append(
                HfFile(
                    filename=sibling.rfilename,
                    size_bytes=getattr(sibling, "size", None),
                )
            )
    # Smaller files first — the admin usually wants the common Q4_K_M
    # quant which is near the middle of the size distribution.
    files.sort(key=lambda f: (f.size_bytes or 0, f.filename))
    return files


@router.post(
    "/download",
    response_model=HfDownloadStarted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_download(
    payload: HfDownloadRequest,
    admin: Annotated[User, Depends(require_admin)],
) -> HfDownloadStarted:
    """Kick off a background download task and return its ID.

    The admin's browser then opens a WebSocket to
    ``/ws/admin/downloads/{download_id}`` to stream progress. Nothing
    is inserted into ``llm_models`` at this stage — that happens in
    ``hf_downloader.download_and_register`` once the bytes actually
    land on disk.
    """
    download_id = spawn_download(payload, admin)
    return HfDownloadStarted(download_id=str(download_id))
