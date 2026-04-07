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

    # LLM
    llm_base_url: str = "http://llm:8080"
    llm_models_dir: str = "/var/lib/llm-models"


settings = Settings()
