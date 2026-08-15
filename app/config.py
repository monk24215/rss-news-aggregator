"""Application configuration.

All configuration comes from environment variables (Non-Negotiable #10: production
secrets never enter source control). Locally, values may be supplied via a .env file;
in production, Railway injects DATABASE_URL and REDIS_URL as service variables.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core service connections (Railway injects these in production).
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    redis_url: str = "redis://localhost:6379/0"

    # Heartbeat interval (seconds) for the Section 1 scheduler → worker proof.
    heartbeat_interval_seconds: int = 30

    # Human-friendly app name for the health page.
    app_name: str = "AI RSS News Aggregator"


@lru_cache
def get_settings() -> Settings:
    return Settings()
