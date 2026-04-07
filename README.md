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

## Related repos

- `testing-agent-explorer` — core crawler (called as subprocess)
- `testing-agent-frontend` — React + Antd UI
- `testing-agent-llm` — llama-swap + llama.cpp
- `testing-agent-infra` — docker-compose stack
