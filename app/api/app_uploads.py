"""App file upload endpoint.

Accepts .app.zip (iOS simulator build), .ipa (iOS archive), or .apk
(Android) files. Extracts the bundle/package identifier from the
binary metadata and stores the extracted app in a shared volume that
the host worker can read directly.

The upload flow is separate from run creation: the user uploads first,
gets an ``upload_id`` + ``bundle_id`` back, then creates a run that
references the upload_id. This lets the frontend show immediate feedback
("valid iOS app, bundle com.example.Foo") before the user clicks Start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import plistlib
import shutil
import zipfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.auth.users import current_active_user, require_tester
from app.config import settings
from app.models.user import User
from app.schemas.app_upload import AppUploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_EXTENSIONS = {".zip", ".ipa", ".apk"}


def _find_app_bundle(extract_dir: Path) -> Path | None:
    """Walk an extracted archive looking for a .app directory with Info.plist."""
    for p in extract_dir.rglob("*.app"):
        if p.is_dir() and (p / "Info.plist").exists():
            return p
    return None


def _read_ios_bundle_info(app_dir: Path) -> tuple[str, str]:
    """Extract (bundle_id, app_name) from a .app/Info.plist."""
    plist_path = app_dir / "Info.plist"
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    bundle_id = info.get("CFBundleIdentifier", "")
    app_name = (
        info.get("CFBundleDisplayName")
        or info.get("CFBundleName")
        or app_dir.stem
    )
    if not bundle_id:
        raise ValueError("CFBundleIdentifier not found in Info.plist")
    return bundle_id, app_name


def _extract_zip(raw_path: Path, extract_dir: Path) -> None:
    """Pure-sync zip extraction — wrapped in asyncio.to_thread by the
    handler so it doesn't block the event loop on big builds (PER-47)."""
    if not zipfile.is_zipfile(raw_path):
        raise ValueError("File is not a valid zip archive")
    with zipfile.ZipFile(raw_path, "r") as zf:
        zf.extractall(extract_dir)


def _read_android_package(apk_path: Path) -> tuple[str, str]:
    """Extract (package, app_name) from an APK's AndroidManifest.xml.

    We try ``aapt2 dump badging`` first (fast, reliable). If aapt2 is
    not available (e.g. backend running in Docker), we fall back to
    reading the binary manifest via a simplified parser.

    Synchronous — the upload handler dispatches it via
    ``asyncio.to_thread`` (PER-47) so subprocess + zip parsing don't
    freeze the event loop.
    """
    import subprocess

    # Try aapt2 / aapt (may be available if Android SDK is mounted)
    for tool in ("aapt2", "aapt"):
        try:
            result = subprocess.run(
                [tool, "dump", "badging", str(apk_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                package = ""
                label = ""
                for line in result.stdout.splitlines():
                    if line.startswith("package:"):
                        for part in line.split():
                            if part.startswith("name='"):
                                package = part.split("'")[1]
                    if "application-label:" in line:
                        label = line.split("'")[1] if "'" in line else ""
                if package:
                    return package, label or package
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Fallback: read package from AndroidManifest.xml inside the zip
    # (binary XML — we look for the package attribute heuristically)
    import re

    with zipfile.ZipFile(apk_path, "r") as zf:
        if "AndroidManifest.xml" in zf.namelist():
            raw = zf.read("AndroidManifest.xml")
            # Binary XML contains UTF-16 strings; look for package-like patterns
            text = raw.decode("utf-8", errors="ignore")
            matches = re.findall(r"[a-z][a-z0-9]*\.[a-z][a-z0-9.]+", text)
            for m in matches:
                if "." in m and len(m) > 5:
                    return m, m

    raise ValueError(
        "Could not extract package name from APK. "
        "Install aapt2 or ensure the APK contains a valid AndroidManifest.xml."
    )


@router.post(
    "/app",
    response_model=AppUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_tester)],
)
async def upload_app(
    file: UploadFile,
    _user: Annotated[User, Depends(current_active_user)],
) -> AppUploadResponse:
    """Upload an app build file (.app.zip, .ipa, .apk).

    Returns the extracted bundle/package identifier so the frontend can
    show it immediately. The upload_id is used later in RunCreateV2 to
    reference this file.
    """
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    # PER-106 #6: the client-supplied multipart filename was used
    # directly as ``upload_dir / file.filename``. A crafted name like
    # ``../../escape.apk`` writes outside the upload directory; later
    # ``app_relative_path`` carries the same string into the worker
    # which joins it with ``uploads_base`` and installs from it.
    # Mitigation:
    #   1. Reduce to a basename (drops any path separators).
    #   2. Reject control characters / non-ASCII path operators.
    #   3. Verify the resolved path still lives under upload_dir
    #      after pathlib resolves any remaining "." / ".." tokens.
    safe_name = Path(file.filename).name  # strips directory components
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(400, "Invalid filename")
    if any(ord(c) < 32 for c in safe_name) or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "Invalid filename — control characters or separators")

    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Read entire file into memory for size check
    content = await file.read()
    if len(content) > settings.app_max_upload_bytes:
        raise HTTPException(
            413,
            f"File too large ({len(content)} bytes). Maximum: {settings.app_max_upload_bytes}.",
        )

    upload_id = str(uuid4())
    uploads_root = Path(settings.app_uploads_dir).resolve()
    upload_dir = uploads_root / upload_id
    # mkdir is synchronous but cheap (one stat + one mkdir syscall) —
    # not worth the to_thread overhead.
    upload_dir.mkdir(parents=True, exist_ok=True)

    raw_path = (upload_dir / safe_name).resolve()
    # Defence in depth — even though safe_name is already a basename
    # and upload_dir is uniquely per-upload, double-check the final
    # path doesn't escape the configured uploads root. ``resolve()``
    # follows any sneaky symlinks too.
    try:
        raw_path.relative_to(uploads_root)
    except ValueError:
        raise HTTPException(400, "Invalid filename — path escapes uploads root")
    # PER-47: write_bytes is sync I/O — for an 8MB build it stalls the
    # event loop ~50ms+, blocking parallel WebSocket frames and run
    # polling. Punt to the default thread pool.
    await asyncio.to_thread(raw_path.write_bytes, content)

    try:
        if ext == ".apk":
            # Android: no extraction needed
            # PER-47: _read_android_package shells out to aapt2 + reads
            # the apk's binary XML — both blocking, push to thread pool.
            bundle_id, app_name = await asyncio.to_thread(
                _read_android_package, raw_path
            )
            app_relative_path = f"{upload_id}/{safe_name}"
            platform = "android"
        else:
            # iOS: .zip or .ipa — both are zip archives containing a .app
            extract_dir = upload_dir / "extracted"
            try:
                # PER-47: zipfile.extractall on an 8MB simulator build
                # spends ~100-300ms in CPU+disk. Off-loop.
                await asyncio.to_thread(_extract_zip, raw_path, extract_dir)
            except ValueError as ve:
                raise HTTPException(400, str(ve))

            app_bundle = _find_app_bundle(extract_dir)
            if app_bundle is None:
                raise HTTPException(
                    400,
                    "No .app bundle with Info.plist found inside the archive. "
                    "For iOS Simulator builds, zip the .app directory directly.",
                )

            # plistlib.load is sync file I/O + parser — short but counts.
            bundle_id, app_name = await asyncio.to_thread(
                _read_ios_bundle_info, app_bundle
            )

            # Move the .app to the top level of the upload dir for easy access
            final_app_path = upload_dir / app_bundle.name
            if final_app_path.exists():
                await asyncio.to_thread(shutil.rmtree, final_app_path)
            # shutil.move on a directory tree blocks for the whole walk.
            await asyncio.to_thread(
                shutil.move, str(app_bundle), str(final_app_path)
            )

            app_relative_path = f"{upload_id}/{app_bundle.name}"
            platform = "ios"

        # Write metadata for the run creation endpoint to read later
        meta = {
            "upload_id": upload_id,
            "bundle_id": bundle_id,
            "app_name": app_name,
            "platform": platform,
            "app_relative_path": app_relative_path,
            "original_filename": safe_name,
        }
        await asyncio.to_thread(
            (upload_dir / "meta.json").write_text, json.dumps(meta, indent=2)
        )

        logger.info(
            "App uploaded: %s → %s (%s, %s)",
            safe_name,
            bundle_id,
            platform,
            upload_id,
        )

        return AppUploadResponse(
            upload_id=upload_id,
            bundle_id=bundle_id,
            app_name=app_name,
            platform=platform,
        )

    except HTTPException:
        raise
    except Exception as exc:
        # Clean up on failure
        await asyncio.to_thread(shutil.rmtree, upload_dir, ignore_errors=True)
        logger.exception("App upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process app file: {exc}",
        ) from exc
