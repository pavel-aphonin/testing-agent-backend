"""HuggingFace model downloader — background task + progress streaming.

The admin clicks "Download" in the HF browser modal, which triggers a
POST to /api/admin/models/hf/download. That endpoint immediately
returns a ``download_id`` and kicks off ``download_and_register`` as
a background asyncio task. Meanwhile the browser opens a WebSocket to
``/ws/admin/downloads/{download_id}``, which subscribes to the Redis
channel we publish to below.

The flow per download:

    1. publish {"type": "download_started", ...}
    2. Resolve file size(s) via HfApi.repo_info(files_metadata=True)
    3. publish periodic {"type": "progress", downloaded, total, ...}
       events as the bytes come in via the custom tqdm class
    4. On success: insert LLMModel row, regenerate llama-swap.yaml,
       publish {"type": "download_complete", model_id}
    5. On failure: publish {"type": "download_failed", error}

We use ``hf_hub_download(..., tqdm_class=...)`` as the progress hook —
it's the only supported public extension point in huggingface_hub 1.9.
``tqdm`` instances are constructed inside the blocking download call
(which runs in a thread pool via ``asyncio.to_thread``) so every
``update()`` call happens off the event loop — we have to bridge back
with ``asyncio.run_coroutine_threadsafe`` to publish progress.

This is not the world's fanciest download manager — no cancel, no
retry, no resume across backend restarts. All deferred to a later
iteration. For now we just need "admin can grab a GGUF from HF without
shelling into the container".
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from uuid import UUID, uuid4

from huggingface_hub import HfApi, hf_hub_download
from sqlalchemy import select
from tqdm import tqdm as _base_tqdm

from app.config import settings
from app.db import async_session_maker
from app.llm_swap import regenerate_swap_config
from app.models.llm_model import LLMModel
from app.models.user import User
from app.redis_bus import channel_for_download, publish_event
from app.schemas.hf_models import HfDownloadRequest

logger = logging.getLogger(__name__)

# How often the tqdm hook is allowed to push a progress event. The
# underlying download calls `update()` once per chunk (a few KB) which
# would flood Redis and the WS — we throttle to roughly one publish per
# 250ms, which is plenty for a smooth progress bar.
PROGRESS_PUBLISH_INTERVAL_SEC = 0.25


# --- custom tqdm that publishes to Redis ------------------------------------


def _make_publishing_tqdm_class(
    loop: asyncio.AbstractEventLoop,
    download_id: UUID,
    file_label: str,
):
    """Build a tqdm subclass bound to this download's Redis channel.

    We construct it as a closure-capturing class so each download gets a
    fresh subclass with its own loop/download_id/file_label baked in.
    The caller passes the resulting class into ``hf_hub_download`` via
    its ``tqdm_class`` kwarg; huggingface_hub will then instantiate it
    internally once per file it downloads and call ``update(n)`` as the
    stream progresses.

    Why we track bytes in ``state`` instead of reading ``self.n``:
    huggingface_hub constructs the progress bar *without* an explicit
    ``disable=`` kwarg (see ``file_download._get_progress_bar_context``)
    which means tqdm falls back to its default of "disable on non-TTY".
    Inside a container with no controlling terminal the bar is
    effectively disabled, and a disabled tqdm's ``update(n)`` short-
    circuits before it bumps ``self.n`` — so reading ``self.n`` would
    always show 0. Maintaining our own counter in the closure sidesteps
    the issue entirely and is the behaviour huggingface_hub's public
    API promises.
    """

    channel = channel_for_download(str(download_id))
    state = {"last_publish": 0.0, "downloaded": 0}

    class _PublishingTqdm(_base_tqdm):
        def update(self, n: int = 1) -> bool:  # type: ignore[override]
            ret = super().update(n)
            # Track our own running total — see class docstring above.
            state["downloaded"] += int(n or 0)
            now = time.monotonic()
            # Always publish the very first update so the UI gets an
            # immediate "we're moving" signal, then throttle afterwards.
            if state["last_publish"] == 0.0 or (
                now - state["last_publish"] >= PROGRESS_PUBLISH_INTERVAL_SEC
            ):
                state["last_publish"] = now
                event = {
                    "type": "progress",
                    "download_id": str(download_id),
                    "file": file_label,
                    "downloaded": state["downloaded"],
                    "total": int(self.total) if self.total else None,
                }
                # We're in a worker thread — schedule the publish on the
                # event loop rather than calling the async helper directly.
                asyncio.run_coroutine_threadsafe(
                    publish_event(channel, event), loop
                )
            return ret

    return _PublishingTqdm


# --- filename → target path -------------------------------------------------


def _target_path_for(filename: str) -> str:
    """Where the GGUF ends up on the shared bind-mount.

    HuggingFace filenames don't collide with each other often, but a
    generic ``mmproj-F16.gguf`` could be published in two different
    repos. We don't try to deduplicate here — callers are expected to
    pick unique names on the admin side. Two models that both request
    ``mmproj-F16.gguf`` will overwrite each other.
    """
    return os.path.join(settings.llm_models_dir, filename)


# --- main entry point --------------------------------------------------------


async def download_and_register(
    payload: HfDownloadRequest,
    download_id: UUID,
    user: User,
) -> None:
    """Download the requested file(s), insert an LLMModel row, regenerate YAML.

    Runs as a background asyncio task spawned from the POST handler. All
    progress/completion/failure signals go through Redis on
    ``channel_for_download(download_id)``; the POST handler does not
    wait for this function to finish.
    """

    channel = channel_for_download(str(download_id))
    loop = asyncio.get_running_loop()

    await publish_event(
        channel,
        {
            "type": "download_started",
            "download_id": str(download_id),
            "repo_id": payload.repo_id,
            "filename": payload.filename,
            "mmproj_filename": payload.mmproj_filename,
        },
    )

    try:
        # --- 1. resolve total size (best-effort) for the main file ---------
        # This is purely to give the UI a "NN GB" hint before bytes start
        # moving. If it fails we just skip the pre-progress event.
        total_bytes: int | None = None
        try:
            api = HfApi()
            info = api.repo_info(payload.repo_id, files_metadata=True)
            for sibling in info.siblings or []:
                if sibling.rfilename == payload.filename:
                    total_bytes = sibling.size
                    break
        except Exception:
            logger.exception(
                "repo_info failed for %s — continuing without size hint",
                payload.repo_id,
            )

        if total_bytes is not None:
            await publish_event(
                channel,
                {
                    "type": "progress",
                    "download_id": str(download_id),
                    "file": payload.filename,
                    "downloaded": 0,
                    "total": total_bytes,
                },
            )

        # --- 2. download main file ------------------------------------------
        main_tqdm = _make_publishing_tqdm_class(loop, download_id, payload.filename)
        # hf_hub_download is blocking — off-thread it. --local-dir-use-symlinks
        # is deprecated in 1.9; the new default already produces real files
        # (not cache symlinks) when local_dir is set, so we don't pass it.
        await asyncio.to_thread(
            hf_hub_download,
            repo_id=payload.repo_id,
            filename=payload.filename,
            local_dir=settings.llm_models_dir,
            tqdm_class=main_tqdm,
        )
        main_path = _target_path_for(payload.filename)

        # --- 3. optionally download mmproj ---------------------------------
        mmproj_path: str | None = None
        if payload.mmproj_filename:
            mmproj_tqdm = _make_publishing_tqdm_class(
                loop, download_id, payload.mmproj_filename
            )
            await asyncio.to_thread(
                hf_hub_download,
                repo_id=payload.repo_id,
                filename=payload.mmproj_filename,
                local_dir=settings.llm_models_dir,
                tqdm_class=mmproj_tqdm,
            )
            mmproj_path = _target_path_for(payload.mmproj_filename)

        # --- 4. insert LLMModel row + regenerate YAML ----------------------
        size_bytes = 0
        try:
            size_bytes = os.path.getsize(main_path)
        except OSError:
            logger.warning("downloaded file vanished before stat: %s", main_path)

        async with async_session_maker() as session:
            model = LLMModel(
                name=payload.name,
                description=payload.description,
                family=payload.family,
                gguf_path=main_path,
                mmproj_path=mmproj_path,
                size_bytes=size_bytes,
                context_length=payload.context_length,
                quantization=payload.quantization,
                supports_vision=payload.supports_vision,
                supports_tool_use=payload.supports_tool_use,
                default_temperature=payload.default_temperature,
                default_top_p=payload.default_top_p,
                is_active=True,
                uploaded_by_user_id=user.id,
                notes=(
                    f"Downloaded from HuggingFace: {payload.repo_id}/"
                    f"{payload.filename}"
                ),
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            try:
                await regenerate_swap_config(session)
            except Exception:
                logger.exception(
                    "Failed to regenerate llama-swap.yaml after HF download"
                )

        await publish_event(
            channel,
            {
                "type": "download_complete",
                "download_id": str(download_id),
                "model_id": str(model.id),
                "model_name": model.name,
                "size_bytes": size_bytes,
            },
        )

    except Exception as exc:
        logger.exception("HF download failed for %s", download_id)
        await publish_event(
            channel,
            {
                "type": "download_failed",
                "download_id": str(download_id),
                "error": str(exc),
            },
        )


# --- registry for in-flight downloads (thin) -------------------------------
#
# Kept deliberately minimal. We only track tasks long enough to avoid
# "coroutine was never awaited" warnings and to let the POST handler spit
# back a download_id. Cancel/status/resume are deferred to a later
# iteration.
_active_downloads: dict[UUID, asyncio.Task[Any]] = {}


def spawn_download(payload: HfDownloadRequest, user: User) -> UUID:
    """Fire-and-forget launch of a download task. Returns its ID."""
    download_id = uuid4()
    task = asyncio.create_task(
        download_and_register(payload, download_id, user),
        name=f"hf-download-{download_id}",
    )
    _active_downloads[download_id] = task
    # Auto-deregister when the task is done, whether success or failure,
    # so the dict doesn't grow unbounded.
    task.add_done_callback(lambda _t: _active_downloads.pop(download_id, None))
    return download_id
