"""Extract + validate an uploaded app bundle (ZIP archive).

Called by /api/apps/upload. The archive is extracted to::

    {app_uploads_dir}/app-bundles/{package_code}/{version}/

The manifest.json is read, parsed against ``AppManifest`` and stored
in the DB. Logo and screenshots are referenced by path for later
serving.

Security: we deny zip-slip attacks (entries escaping the target
directory) and reject bundles over 20 MB.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.config import settings
from app.schemas.app_package import AppManifest

MAX_BUNDLE_BYTES = 20 * 1024 * 1024  # 20 MB
BUNDLES_SUBDIR = "app-bundles"


@dataclass
class ExtractedBundle:
    manifest: AppManifest
    bundle_dir: Path          # absolute
    bundle_relpath: str       # relative to app_uploads_dir (for DB)
    logo_relpath: str | None  # relative to app_uploads_dir
    cover_relpath: str | None
    size_bytes: int


class BundleError(ValueError):
    """Raised on any malformed or unsafe bundle."""


def extract_and_validate(zip_bytes: bytes) -> ExtractedBundle:
    """Unpack + validate. Raises BundleError on any issue.

    Target path is derived from manifest.code + manifest.version; two
    uploads of the same (code, version) clobber each other — callers
    must check the DB uniqueness first if that matters.
    """
    size_bytes = len(zip_bytes)
    if size_bytes > MAX_BUNDLE_BYTES:
        raise BundleError(f"Архив слишком большой (> {MAX_BUNDLE_BYTES // 1024 // 1024} МБ)")

    # Write the zip to a temp file first (zipfile wants a path or stream;
    # we could use BytesIO but the file on disk simplifies debugging).
    tmp_zip = Path(settings.app_uploads_dir) / BUNDLES_SUBDIR / "_tmp_upload.zip"
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip.write_bytes(zip_bytes)

    try:
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            # Look for manifest.json at the root (or in a single top-level folder
            # — many OS zip tools wrap archives in a folder named after the
            # project; we strip that prefix if it's the only top-level entry).
            manifest_text, prefix = _find_manifest(zf)

            try:
                manifest_obj = AppManifest(**json.loads(manifest_text))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise BundleError(f"Невалидный manifest.json: {exc}") from exc

            # Prepare target dir. If a bundle with the same code+version
            # already exists we wipe it — the DB uniqueness check has to
            # be done by the caller before we get here.
            target_dir = (
                Path(settings.app_uploads_dir)
                / BUNDLES_SUBDIR
                / manifest_obj.code
                / manifest_obj.version
            )
            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)

            # Extract files, strip the wrapping folder prefix if present,
            # refuse any entry that would escape the target dir.
            logo_rel: str | None = None
            cover_rel: str | None = None
            screenshots: list[str] = []
            for info in zf.infolist():
                name = info.filename
                if prefix and name.startswith(prefix):
                    name = name[len(prefix):]
                if not name or name.endswith("/"):
                    continue
                # Reject absolute or .. paths
                if name.startswith("/") or ".." in Path(name).parts:
                    raise BundleError(f"Недопустимый путь в архиве: {info.filename}")

                out_path = target_dir / name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(zf.read(info))

                if Path(name).name in ("logo.png", "logo.jpg", "logo.jpeg", "logo.svg"):
                    logo_rel = f"{BUNDLES_SUBDIR}/{manifest_obj.code}/{manifest_obj.version}/{name}"
                if Path(name).name in ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp"):
                    cover_rel = f"{BUNDLES_SUBDIR}/{manifest_obj.code}/{manifest_obj.version}/{name}"
                # Auto-discover screenshots from the screenshots/ folder.
                parts = Path(name).parts
                if parts and parts[0] == "screenshots":
                    ext = Path(name).suffix.lower()
                    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
                        screenshots.append(name)

            # Attach screenshots to the manifest (if author didn't list them
            # explicitly, we fill from autodiscover).
            if not manifest_obj.screenshots and screenshots:
                from app.schemas.app_package import ManifestScreenshot
                manifest_obj.screenshots = [
                    ManifestScreenshot(path=p) for p in sorted(screenshots)
                ]

            # Fall back to CHANGELOG.md for release notes if manifest
            # didn't specify them inline.
            if not manifest_obj.changelog:
                cl_path = target_dir / "CHANGELOG.md"
                if cl_path.exists():
                    try:
                        manifest_obj.changelog = cl_path.read_text(encoding="utf-8")[:4000]
                    except OSError:
                        pass

            # Sanity: manifest references must exist
            for slot in manifest_obj.ui_slots:
                if not (target_dir / "frontend" / slot.path).exists() \
                        and not (target_dir / slot.path).exists():
                    raise BundleError(
                        f"UI slot '{slot.label}' ссылается на {slot.path}, "
                        f"но файл не найден в архиве"
                    )

            return ExtractedBundle(
                manifest=manifest_obj,
                bundle_dir=target_dir,
                bundle_relpath=f"{BUNDLES_SUBDIR}/{manifest_obj.code}/{manifest_obj.version}",
                logo_relpath=logo_rel,
                cover_relpath=cover_rel,
                size_bytes=size_bytes,
            )
    finally:
        try:
            tmp_zip.unlink()
        except OSError:
            pass


def _find_manifest(zf: zipfile.ZipFile) -> tuple[str, str]:
    """Return (manifest text, top-level-prefix-or-empty).

    Accepts manifest.json at the archive root or inside a single
    top-level directory (common when tools like Finder / Keka wrap
    contents in a folder named after the source).
    """
    for name in zf.namelist():
        if name == "manifest.json":
            return zf.read(name).decode("utf-8"), ""
    # Look one level deep
    top_levels = {n.split("/", 1)[0] for n in zf.namelist() if "/" in n}
    for top in top_levels:
        candidate = f"{top}/manifest.json"
        if candidate in zf.namelist():
            return zf.read(candidate).decode("utf-8"), f"{top}/"
    raise BundleError("manifest.json не найден в корне архива")
