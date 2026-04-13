# testing-agent-backend

FastAPI backend for Testing Agent. Orchestrates exploration runs, manages users and LLM models, streams live progress over WebSocket.

## Stack

- **FastAPI** — HTTP + WebSocket
- **fastapi-users** — JWT auth, role model
- **SQLAlchemy 2.0 + Alembic** — Postgres ORM and migrations
- **Redis** — pub/sub bridge between explorer subprocess and WebSocket clients
- **asyncpg / psycopg** — async Postgres driver

## Role model

- **viewer** — read-only access to own runs and profile
- **tester** — create runs, edit own profile and settings
- **admin** — manage users, manage LLM models, full control over all runs

## Endpoints (planned)

```
POST   /auth/login              Login with email + password
POST   /auth/refresh            Refresh JWT
GET    /auth/me                 Current user

GET    /api/runs                List my runs (admin: all)
POST   /api/runs                Start new exploration run
GET    /api/runs/{id}           Run details
GET    /api/runs/{id}/graph     Full graph JSON
DELETE /api/runs/{id}           Delete run
WS     /ws/runs/{id}            Live progress stream

GET    /api/models              Active LLM models (for tester New Run dropdown)
POST   /api/admin/models        [admin] Upload GGUF + metadata
GET    /api/admin/models        [admin] All models
PUT    /api/admin/models/{id}   [admin] Edit model
DELETE /api/admin/models/{id}   [admin] Remove model
POST   /api/admin/models/{id}/bench   [admin] Run benchmark

GET    /api/admin/users         [admin] List users
POST   /api/admin/users         [admin] Create user
PUT    /api/admin/users/{id}    [admin] Edit user (role, reset password)
DELETE /api/admin/users/{id}    [admin] Delete user

GET    /api/settings            My agent settings
PUT    /api/settings            Update my agent settings
GET    /api/profile             My profile
PUT    /api/profile             Update my profile
```

## Database migrations

Alembic is the source of truth for the Postgres schema. The backend
runs `alembic upgrade head` automatically on startup (in the FastAPI
lifespan, via `run_in_executor` because alembic's async env wraps
`asyncio.run`). There is no `Base.metadata.create_all` anywhere.

### Applying migrations manually

Inside a running stack (normally unnecessary — lifespan does it):
```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
```

### Creating a new migration

Alembic's `--autogenerate` compares the live `target_metadata` to a
real Postgres database. You cannot run it against the dev database
because that DB is already "at head" — autogenerate would produce an
empty diff. Use a throwaway Postgres on a different port so the dev
volume is untouched:

```bash
# 1. Spin up a scratch postgres on :5433
docker run --rm -d --name alembic-tmp \
  -e POSTGRES_USER=ta -e POSTGRES_PASSWORD=ta -e POSTGRES_DB=ta \
  -p 5433:5432 postgres:16
sleep 3

# 2. Upgrade the scratch DB to current head first (so autogenerate
#    only sees the diff from the last real migration, not the whole
#    schema again)
docker compose run --rm \
  -e DATABASE_URL=postgresql+asyncpg://ta:ta@host.docker.internal:5433/ta \
  backend alembic upgrade head

# 3. Generate the new revision
docker compose run --rm \
  -e DATABASE_URL=postgresql+asyncpg://ta:ta@host.docker.internal:5433/ta \
  backend alembic revision --autogenerate -m "add foo column"

# 4. Review the generated file under alembic/versions/ — autogenerate
#    misses a lot (enum alters, column renames, index reorders).
#    Fix it by hand before committing.

# 5. Teardown
docker rm -f alembic-tmp
```

`host.docker.internal` resolves the host from inside the backend
container (Docker Desktop on macOS supports this out of the box; on
Linux you may need `--add-host host.docker.internal:host-gateway`).

### Downgrades

`alembic downgrade` is wired but not used in development — the bare
initial schema's downgrade is `drop_all`, which is not useful on a
populated DB. For feature rollbacks write a targeted migration
instead of relying on downgrade.

## Related repos

- `testing-agent-explorer` — core crawler (called as subprocess)
- `testing-agent-frontend` — React + Antd UI
- `testing-agent-llm` — llama-swap + llama.cpp
- `testing-agent-infra` — docker-compose stack
