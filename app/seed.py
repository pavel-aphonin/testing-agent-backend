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
from app.models.user import User, UserRole
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
        # unsloth/gemma-4-E4B-it-GGUF — capital 'E4B' matters on Linux.
        "gguf_path": "/var/lib/llm-models/gemma-4-E4B-it-Q4_K_M.gguf",
        "mmproj_path": "/var/lib/llm-models/gemma-4-E4B-it-mmproj-F16.gguf",
        "size_bytes": 5_100_000_000,  # ~4.98 GB
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
        # unsloth/Qwen3.5-35B-A3B-GGUF — 'UD-' is the Unsloth Dynamic prefix.
        "gguf_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf",
        "mmproj_path": "/var/lib/llm-models/Qwen3.5-35B-A3B-mmproj-F16.gguf",
        "size_bytes": 22_200_000_000,  # ~22.2 GB
        "context_length": 262_144,
        "quantization": "UD-Q4_K_XL",
        "supports_vision": True,
        "supports_tool_use": True,
        "default_temperature": 0.6,
        "default_top_p": 0.9,
    },
]


async def seed_initial_admin() -> None:
    """Create the first admin if the users table is empty."""
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
                    role=UserRole.ADMIN.value,
                    must_change_password=False,
                )
            )
            print(f"[seed] Created initial admin: {user.email}")
        except UserAlreadyExists:
            print(f"[seed] Admin {settings.initial_admin_email} already exists.")


async def seed_initial_models() -> None:
    """Insert the two pre-installed LLM models if they're not in the table yet.

    Idempotent: looks each model up by name and skips inserts that already
    exist. After any changes (or even with no changes), regenerates the
    llama-swap.yaml so the llm container always has a fresh config to read.
    """
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

        # Always regenerate the swap config — even if no inserts happened, the
        # backend may be starting against a fresh volume that has no yaml yet.
        # Failures here are non-fatal: log and move on so the API stays usable.
        try:
            await regenerate_swap_config(session)
            print(f"[seed] Wrote {settings.llm_swap_config_path}")
        except Exception as exc:  # pragma: no cover - filesystem edge cases
            print(f"[seed] Failed to write swap config: {exc}")
