"""Seed script: on first startup, create the initial admin and the two
pre-installed LLM models (Gemma 4 E4B + Qwen 3.5 35B-A3B) if they're missing.

Both models were released by Google DeepMind / Alibaba on April 7, 2026
and are downloaded into LLM_MODELS_DIR by ``make download-models``. The
filenames here MUST match what that script writes to disk, otherwise
llama-swap won't be able to spawn the corresponding llama-server process.
"""

from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select

from app.auth.users import UserManager
from app.config import settings
from app.db import async_session_maker
from app.llm_swap import regenerate_swap_config
from app.models.llm_model import LLMModel
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserCreate


INITIAL_MODELS = [
    {
        "name": "gemma-4-e4b",
        "family": "gemma-4",
        "description": (
            "Gemma 4 E4B (4.5B effective params, 8B with embeddings). "
            "Released April 7, 2026 by Google DeepMind. 128K context, "
            "vision input, tool use, audio, 140+ languages. The fast "
            "classifier in Hybrid mode — sets PUCT priors over UI elements."
        ),
        "gguf_path": "/var/lib/llm-models/gemma-4-E4B-it-Q4_K_M.gguf",
        "mmproj_path": "/var/lib/llm-models/gemma-4-E4B-it-mmproj-F16.gguf",
        "size_bytes": 5_100_000_000,
        "context_length": 131_072,
        "quantization": "Q4_K_M",
        "supports_vision": True,
        "supports_tool_use": True,
        "default_temperature": 0.4,
        "default_top_p": 0.9,
    },
    {
        "name": "qwen3.5-35b-a3b",
        "family": "qwen-3.5",
        "description": (
            "Qwen 3.5 35B-A3B (MoE: 35B total, 3B active per token). "
            "Released April 7, 2026 by Alibaba. Inference speed close to a "
            "dense 3B model despite the 35B parameter count. 262K context, "
            "vision, tool use, reasoning. The smart actor in AI mode and "
            "the analyzer for Phase 2 graph review."
        ),
        "gguf_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf",
        "mmproj_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-mmproj-F16.gguf",
        "size_bytes": 22_200_000_000,
        "context_length": 262_144,
        "quantization": "UD-Q4_K_XL",
        "supports_vision": True,
        "supports_tool_use": True,
        "default_temperature": 0.6,
        "default_top_p": 0.9,
    },
]


async def seed_initial_admin() -> None:
    """Create the first admin if the users table is empty.

    After creating the user, link them to the system 'admin' role.
    """
    async with async_session_maker() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            print("[seed] Users already exist, skipping admin seed.")
            return

    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)

        try:
            user = await user_manager.create(
                UserCreate(
                    email=settings.initial_admin_email,
                    password=settings.initial_admin_password,
                    is_superuser=True,
                    is_verified=True,
                    role="admin",
                    must_change_password=False,
                )
            )
            # Link to the system admin role
            admin_role = await session.execute(
                select(Role).where(Role.code == "admin")
            )
            role_obj = admin_role.scalar_one_or_none()
            if role_obj:
                result = await session.execute(
                    select(User).where(User.id == user.id)
                )
                user_obj = result.scalar_one()
                user_obj.role_id = role_obj.id
                await session.commit()

            print(f"[seed] Created initial admin: {user.email}")
        except UserAlreadyExists:
            print(f"[seed] Admin {settings.initial_admin_email} already exists.")


async def seed_demo_apps() -> None:
    """Pack and register every bundle in app/seed_apps/ as an approved
    public app. Idempotent — skips apps that already exist."""
    import io
    import json as _json
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path

    from app.models.app_package import (
        AppApprovalStatus,
        AppPackage,
        AppPackageVersion,
    )
    from app.services.app_bundle import extract_and_validate

    apps_root = Path(__file__).parent / "seed_apps"
    if not apps_root.exists():
        return

    async with async_session_maker() as session:
        for seed_dir in sorted(apps_root.iterdir()):
            if not seed_dir.is_dir() or not (seed_dir / "manifest.json").exists():
                continue

            # Peek at the manifest to check uniqueness before doing work.
            try:
                manifest_raw = _json.loads((seed_dir / "manifest.json").read_text())
                code = manifest_raw.get("code")
            except Exception:  # noqa: BLE001
                continue
            if not code:
                continue

            q = await session.execute(select(AppPackage).where(AppPackage.code == code))
            existing_pkg = q.scalar_one_or_none()

            # Skip only if a version row with the manifest's version
            # already exists — allows bumping the version in seed_apps
            # to publish an upgrade.
            manifest_version = manifest_raw.get("version")
            if existing_pkg and manifest_version:
                from app.models.app_package import AppPackageVersion as _V
                vq = await session.execute(
                    select(_V).where(
                        _V.app_package_id == existing_pkg.id,
                        _V.version == manifest_version,
                    )
                )
                if vq.scalar_one_or_none() is not None:
                    continue  # this exact version already seeded

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in seed_dir.rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(seed_dir).as_posix())
            zip_bytes = buf.getvalue()

            try:
                extracted = extract_and_validate(zip_bytes)
            except Exception as exc:  # noqa: BLE001
                print(f"[seed] {code} extract failed: {exc}")
                continue

            if existing_pkg:
                # Upgrade path: same code, new version. Update metadata.
                existing_pkg.name = extracted.manifest.name
                existing_pkg.description = extracted.manifest.description
                existing_pkg.category = extracted.manifest.category
                existing_pkg.author = extracted.manifest.author
                if extracted.logo_relpath:
                    existing_pkg.logo_path = extracted.logo_relpath
                pkg = existing_pkg
            else:
                pkg = AppPackage(
                    code=extracted.manifest.code,
                    name=extracted.manifest.name,
                    description=extracted.manifest.description,
                    category=extracted.manifest.category,
                    author=extracted.manifest.author,
                    logo_path=extracted.logo_relpath,
                    is_public=True,
                    approval_status=AppApprovalStatus.APPROVED.value,
                    approved_at=datetime.now(timezone.utc),
                )
                session.add(pkg)
            await session.flush()
            version = AppPackageVersion(
                app_package_id=pkg.id,
                version=extracted.manifest.version,
                manifest=extracted.manifest.model_dump(),
                bundle_path=extracted.bundle_relpath,
                size_bytes=extracted.size_bytes,
            )
            session.add(version)
            await session.commit()
            print(f"[seed] Registered app: {pkg.name} v{version.version}")


async def seed_initial_models() -> None:
    """Insert the two pre-installed LLM models if they're not in the table yet."""
    inserted_any = False
    async with async_session_maker() as session:
        for spec in INITIAL_MODELS:
            existing = await session.execute(
                select(LLMModel).where(LLMModel.name == spec["name"])
            )
            if existing.scalar_one_or_none() is not None:
                print(f"[seed] LLM model {spec['name']} already exists.")
                continue
            session.add(LLMModel(**spec, is_active=True))
            inserted_any = True
            print(f"[seed] Created LLM model: {spec['name']}")
        await session.commit()

        try:
            await regenerate_swap_config(session)
            print(f"[seed] Wrote {settings.llm_swap_config_path}")
        except Exception as exc:
            print(f"[seed] Failed to write swap config: {exc}")
