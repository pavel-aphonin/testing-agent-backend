"""Application configuration loaded from environment variables."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PER-136: explicit dev/prod switch. In ``dev`` the validators
    # below soften their checks (admin@example.com / weak passwords
    # are allowed with a warning) so first-time local setup just
    # works. In ``prod`` they enforce real-credential policy.
    # Default ``dev`` keeps the developer experience friendly; CI/
    # production deployments set DEPLOYMENT_MODE=prod in their env.
    deployment_mode: str = "dev"

    # Database
    database_url: str = "postgresql+asyncpg://testing_agent:testing_agent@postgres:5432/testing_agent"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # JWT — empty default + validator below, same policy as worker_token.
    # A previous placeholder "change_me" was a P1 security finding
    # (PER-106 #8) because environments that didn't override the env
    # var would have signed tokens with a publicly-known secret.
    jwt_secret: str = ""
    jwt_access_token_expires_min: int = 15
    jwt_refresh_token_expires_days: int = 7

    # Initial admin (created by seed on first startup if no users exist).
    # Both fields must be explicit — placeholders / common-defaults are
    # rejected by the validators below to force the operator to choose
    # a real address and password rather than ship the seed defaults.
    initial_admin_email: str = ""
    initial_admin_password: str = ""

    # LLM (chat completions) — used by the agent during exploration.
    # Default points at Gemma (small, fast, multimodal with mmproj).
    llm_base_url: str = "http://llm:8080"
    # LLM for RAG answer generation — separate from the agent's chat LLM
    # because RAG benefits from larger/better models (Qwen3-8B Instruct).
    # Empty → falls back to llm_base_url.
    rag_llm_base_url: str = ""
    # Embeddings: by default we share the chat URL, but on macOS we run
    # a separate llama-server with a real embedding model (Qwen3-Embedding-8B)
    # on a second port because Gemma's 2560-dim embeddings exceed pgvector's
    # HNSW max (2000).
    embedding_base_url: str = ""  # empty → falls back to llm_base_url
    # Reranker (Qwen3-Reranker-8B) — optional second stage after vector search.
    # Empty → reranking is skipped and RAG uses vector-search order directly.
    reranker_base_url: str = ""
    llm_models_dir: str = "/var/lib/llm-models"
    # llama-swap reads this YAML and watches it for changes; backend regenerates
    # it after every CRUD on llm_models. Lives in the shared bind-mount so the
    # llm container can see it without any cross-container coordination.
    llm_swap_config_path: str = "/var/lib/llm-models/llama-swap.yaml"
    # The base port for per-model llama-server processes spawned by llama-swap.
    # The N-th model in the table gets port (llm_swap_base_port + N).
    llm_swap_base_port: int = 8180

    # Worker (explorer daemon running on the host)
    # Workers send WORKER_TOKEN as a Bearer token to /api/internal/* endpoints.
    # Generate with: openssl rand -hex 32
    #
    # NO public default. The previous placeholder was a P1 security
    # finding (PER-104) — any environment that didn't override the env
    # var would have left /api/internal/* protected by a known string.
    # Empty here makes "config missing" explicit; the validator below
    # refuses to load Settings without an env-supplied value.
    worker_token: str = ""

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_required(cls, v: str) -> str:
        """Reject empty / placeholder JWT signing keys.

        Same policy as worker_token (PER-106 #8): an unset env should
        fail loudly at boot rather than ship with a known-publicly
        secret. Generate with ``openssl rand -hex 32``.
        """
        if not v or v.strip() == "":
            raise ValueError(
                "JWT_SECRET env var is required. Generate with "
                "`openssl rand -hex 32` and set it in the backend's "
                "environment. No default is supplied — this protects "
                "issued JWTs from being signed with a known key."
            )
        if v in ("change_me", "change_me_to_a_32_byte_hex_string"):
            raise ValueError(
                "JWT_SECRET is set to a historical placeholder value. "
                "Generate a real one with `openssl rand -hex 32`."
            )
        if len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters. Generate "
                "with `openssl rand -hex 32` (produces 64 hex chars)."
            )
        return v

    @field_validator("initial_admin_email")
    @classmethod
    def _admin_email_required(cls, v: str, info) -> str:
        # PER-136: empty / blank email is always rejected — the seed
        # has nothing to create without it. The ``admin@example.com``
        # placeholder is rejected only in ``prod`` mode; in ``dev``
        # we let it through with a logger warning so first-time setup
        # is friction-free. Operators flipping to prod will see the
        # error on their next deploy if they didn't change it.
        import logging
        if not v or v.strip() == "":
            raise ValueError(
                "INITIAL_ADMIN_EMAIL env var is required. The first-run "
                "seed creates a single admin account with this email."
            )
        mode = (info.data.get("deployment_mode") or "dev").lower()
        if v == "admin@example.com":
            if mode == "prod":
                raise ValueError(
                    "INITIAL_ADMIN_EMAIL is the placeholder "
                    "'admin@example.com'. Use a real address you control "
                    "(DEPLOYMENT_MODE=prod forbids placeholders)."
                )
            logging.getLogger(__name__).warning(
                "INITIAL_ADMIN_EMAIL is set to 'admin@example.com' — "
                "fine for dev, but flip DEPLOYMENT_MODE=prod and use a "
                "real address before any non-local deployment."
            )
        return v

    @field_validator("initial_admin_password")
    @classmethod
    def _admin_password_required(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError(
                "INITIAL_ADMIN_PASSWORD env var is required. The "
                "first-run seed creates the admin with this password; "
                "the seed used to default to 'change_me' (PER-106 #8)."
            )
        if v in ("change_me", "admin", "password", "123456"):
            raise ValueError(
                "INITIAL_ADMIN_PASSWORD is a common placeholder. "
                "Pick something non-obvious — `openssl rand -base64 24` "
                "is a reasonable starting point."
            )
        return v

    @field_validator("worker_token")
    @classmethod
    def _worker_token_required(cls, v: str) -> str:
        """Refuse to start without an explicit, non-placeholder token.

        Rejects the historical placeholder value too, so a stale .env
        carrying the old default fails loudly instead of silently
        running with a publicly-known credential.
        """
        if not v or v.strip() == "":
            raise ValueError(
                "WORKER_TOKEN env var is required. Generate with "
                "`openssl rand -hex 32` and set it in the backend's "
                "environment. No default is supplied — this protects "
                "/api/internal/* from accidental exposure."
            )
        if v == "change_me_worker_token_long_random_string":
            raise ValueError(
                "WORKER_TOKEN is set to the historical placeholder value. "
                "Generate a real one with `openssl rand -hex 32` and "
                "update the backend env. This check exists because the "
                "placeholder leaked into infra/.env and worker CLI "
                "defaults — leaving it in production would allow anyone "
                "with the placeholder to call /api/internal/*."
            )
        return v

    # App uploads (shared volume with host worker)
    app_uploads_dir: str = "/var/lib/app-uploads"
    app_max_upload_bytes: int = 500_000_000  # 500 MB

    # RAG / embeddings
    # Which model name (as registered with llama-swap) to ask for
    # text embeddings. EmbeddingClient retries the embedding endpoint
    # three times and then raises if it stays unreachable — the
    # previous hash-based fallback was removed because silently-fake
    # vectors corrupted similarity scores in pgvector.
    embedding_model_name: str = "embeddings"
    embedding_request_timeout_sec: float = 10.0


settings = Settings()
