"""Testing Agent backend — FastAPI entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin_users as admin_users_api
from app.api import assistant as assistant_api
from app.api import roles as roles_api
from app.api import defects as defects_api
from app.api import devices as devices_api
from app.api import agent_settings as agent_settings_api
from app.api import app_uploads as app_uploads_api
from app.api import download_ws as download_ws_api
from app.api import hf_models as hf_models_api
from app.api import internal_runs as internal_runs_api
from app.api import knowledge as knowledge_api
from app.api import llm_models as llm_models_api
from app.api import run_mirror as run_mirror_api
from app.api import profile as profile_api
from app.api import run_ws as run_ws_api
from app.api import runs as runs_api
from app.api import scenarios as scenarios_api
from app.api import test_data as test_data_api
from app.api import worker_status as worker_status_api
from app.api import workspaces as workspaces_api
from app.api import notifications as notifications_api
from app.api import attributes as attributes_api
from app.api import custom_dictionaries as custom_dicts_api
from app.auth.users import auth_backend, fastapi_users
from app.db import engine
from app.models import (  # noqa: F401  registers all tables on Base.metadata
    AgentSettings,
    Attribute,
    AttributeValue,
    CustomDictionary,
    CustomDictionaryItem,
    CustomDictionaryPermission,
    DefectModel,
    DeviceConfig,
    Edge,
    KnowledgeChunk,
    KnowledgeDocument,
    LLMModel,
    Role,
    Run,
    Notification,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    Scenario,
    Screen,
    TestData,
    User,
)
from app.schemas.user import UserRead, UserUpdate
from app.seed import seed_initial_admin, seed_initial_models

logger = logging.getLogger(__name__)


def _run_migrations_sync() -> None:
    """Run `alembic upgrade head` synchronously.

    Must be called from a worker thread (via run_in_executor), NOT
    from the main event loop: alembic's env.py wraps its online
    migration runner in ``asyncio.run()``, which would blow up with
    "asyncio.run() cannot be called from a running event loop" if
    invoked directly from the FastAPI lifespan coroutine.
    """
    # Relative path resolves against the container's WORKDIR (/app),
    # which is where the Dockerfile copies alembic.ini to.
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: apply migrations, then seed.
    logger.info("Running alembic upgrade head…")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_migrations_sync)
    logger.info("Migrations complete.")

    await seed_initial_admin()
    await seed_initial_models()
    yield
    await engine.dispose()


app = FastAPI(
    title="Testing Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Auth routes -------------------------------------------------------------
# POST /auth/jwt/login  — email + password → JWT
# POST /auth/jwt/logout — invalidate (client-side discard)
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

# NO register router — admins create users, not self-registration.

# GET    /users/me
# PATCH  /users/me
# GET    /users/{id}         (superuser)
# PATCH  /users/{id}         (superuser)
# DELETE /users/{id}         (superuser)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)

# --- Application API ---------------------------------------------------------
app.include_router(runs_api.router)
app.include_router(app_uploads_api.router)
app.include_router(devices_api.public_router)
app.include_router(devices_api.admin_router)
app.include_router(admin_users_api.router)
app.include_router(llm_models_api.admin_router)
app.include_router(llm_models_api.public_router)
app.include_router(agent_settings_api.router)
app.include_router(profile_api.router)
app.include_router(internal_runs_api.router)
app.include_router(hf_models_api.router)
app.include_router(knowledge_api.router)
app.include_router(run_mirror_api.router)
app.include_router(scenarios_api.router)
app.include_router(test_data_api.router)
app.include_router(defects_api.public_router)
app.include_router(defects_api.internal_router)
app.include_router(worker_status_api.public_router)
app.include_router(worker_status_api.internal_router)
app.include_router(assistant_api.router)
app.include_router(roles_api.router)
app.include_router(workspaces_api.router)
app.include_router(workspaces_api.admin_router)
app.include_router(notifications_api.router)
app.include_router(notifications_api.inv_router)
app.include_router(attributes_api.attr_router)
app.include_router(attributes_api.val_router)
app.include_router(custom_dicts_api.router)
app.include_router(run_ws_api.router)
app.include_router(download_ws_api.router)
