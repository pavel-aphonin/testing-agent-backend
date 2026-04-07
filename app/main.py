"""Testing Agent backend — FastAPI entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.users import auth_backend, fastapi_users
from app.db import Base, engine
from app.models import (  # noqa: F401  registers all tables on Base.metadata
    AgentSettings,
    Edge,
    LLMModel,
    Run,
    Screen,
    User,
)
from app.schemas.user import UserRead, UserUpdate
from app.seed import seed_initial_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables (Alembic will replace this in a later iteration)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_initial_admin()
    yield
    await engine.dispose()


app = FastAPI(
    title="Testing Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
