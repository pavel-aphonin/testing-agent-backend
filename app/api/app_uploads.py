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


def _read_android_package(apk_path: Path) -> tuple[str, str]:
    """Extract (package, app_name) from an APK's AndroidManifest.xml.

    We try ``aapt2 dump badging`` first (fast, reliable). If aapt2 is
    not available (e.g. backend running in Docker), we fall back to
    reading the binary manifest via a simplified parser.
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

    ext = Path(file.filename).suffix.lower()
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
    upload_dir = Path(settings.app_uploads_dir) / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    raw_path = upload_dir / file.filename
    raw_path.write_bytes(content)

    try:
        if ext == ".apk":
            # Android: no extraction needed
            bundle_id, app_name = _read_android_package(raw_path)
            app_relative_path = f"{upload_id}/{file.filename}"
            platform = "android"
        else:
            # iOS: .zip or .ipa — both are zip archives containing a .app
            if not zipfile.is_zipfile(raw_path):
                raise HTTPException(400, "File is not a valid zip archive")

            extract_dir = upload_dir / "extracted"
            with zipfile.ZipFile(raw_path, "r") as zf:
                zf.extractall(extract_dir)

            app_bundle = _find_app_bundle(extract_dir)
            if app_bundle is None:
                raise HTTPException(
                    400,
                    "No .app bundle with Info.plist found inside the archive. "
                    "For iOS Simulator builds, zip the .app directory directly.",
                )

            bundle_id, app_name = _read_ios_bundle_info(app_bundle)

            # Move the .app to the top level of the upload dir for easy access
            final_app_path = upload_dir / app_bundle.name
            if final_app_path.exists():
                shutil.rmtree(final_app_path)
            shutil.move(str(app_bundle), str(final_app_path))

            app_relative_path = f"{upload_id}/{app_bundle.name}"
            platform = "ios"

        # Write metadata for the run creation endpoint to read later
        meta = {
            "upload_id": upload_id,
            "bundle_id": bundle_id,
            "app_name": app_name,
            "platform": platform,
            "app_relative_path": app_relative_path,
            "original_filename": file.filename,
        }
        (upload_dir / "meta.json").write_text(json.dumps(meta, indent=2))

        logger.info(
            "App uploaded: %s → %s (%s, %s)",
            file.filename,
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
        shutil.rmtree(upload_dir, ignore_errors=True)
        logger.exception("App upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process app file: {exc}",
        ) from exc
