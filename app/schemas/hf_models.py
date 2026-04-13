"""Pydantic schemas for the HuggingFace model browser.

The admin UI calls three endpoints against these schemas:

    GET  /api/admin/models/hf/search                      → list[HfRepoSummary]
    GET  /api/admin/models/hf/repo/{owner}/{name}/files   → list[HfFile]
    POST /api/admin/models/hf/download                    → HfDownloadStarted

Progress updates after a POST flow over a WebSocket instead of HTTP, so
those event payloads live inline in app/api/download_ws.py rather than
here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HfRepoSummary(BaseModel):
    """One row in the "search HuggingFace" result list.

    Fields are deliberately a small subset of what ``HfApi.list_models``
    returns — just enough to render a table and let the admin pick a repo.
    """

    repo_id: str
    downloads: int | None = None
    likes: int | None = None
    last_modified: datetime | None = None
    library_name: str | None = None
    tags: list[str] = Field(default_factory=list)


class HfFile(BaseModel):
    """One .gguf file inside a repository."""

    filename: str
    size_bytes: int | None = None


class HfDownloadRequest(BaseModel):
    """POST body for starting a download + auto-registration flow.

    The admin fills in ``name``/``family``/etc. themselves in the modal
    — we don't try to parse metadata out of the GGUF header. Heuristics
    on the frontend can pre-fill these from the filename, but the source
    of truth is what gets POSTed here.
    """

    repo_id: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    mmproj_filename: str | None = None

    # LLMModel fields — mirrors LLMModelCreate but with defaults sized for
    # a typical mid-range GGUF so the admin form can stay small.
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    family: str = Field(..., min_length=1, max_length=50)
    context_length: int = Field(default=4096, ge=128, le=1_000_000)
    quantization: str = Field(..., min_length=1, max_length=20)
    supports_vision: bool = False
    supports_tool_use: bool = False
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=0.9, ge=0.0, le=1.0)


class HfDownloadStarted(BaseModel):
    """Response from POST /download: ID the client uses to open the WS."""

    download_id: str
