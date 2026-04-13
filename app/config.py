"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://testing_agent:testing_agent@postgres:5432/testing_agent"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # JWT
    jwt_secret: str = "change_me"
    jwt_access_token_expires_min: int = 15
    jwt_refresh_token_expires_days: int = 7

    # Initial admin (created by seed on first startup if no users exist)
    initial_admin_email: str = "admin@example.com"
    initial_admin_password: str = "change_me"

    # LLM (chat completions)
    llm_base_url: str = "http://llm:8080"
    # Embeddings: by default we share the chat URL, but on macOS we run
    # a separate llama-server with a real embedding model (bge-small) on
    # a second port because Gemma's 2560-dim embeddings exceed pgvector's
    # HNSW max (2000) and don't match our `vector(384)` schema.
    embedding_base_url: str = ""  # empty → falls back to llm_base_url
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
    worker_token: str = "change_me_worker_token_long_random_string"

    # App uploads (shared volume with host worker)
    app_uploads_dir: str = "/var/lib/app-uploads"
    app_max_upload_bytes: int = 500_000_000  # 500 MB

    # RAG / embeddings
    # Which model name (as registered with llama-swap) to ask for
    # text embeddings. If the LLM is unreachable, the EmbeddingClient
    # falls back to a deterministic hash-based embedding so the rest of
    # the pipeline keeps working — this is clearly marked as fake in logs.
    embedding_model_name: str = "embeddings"
    embedding_request_timeout_sec: float = 10.0


settings = Settings()
