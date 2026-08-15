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

    # --- AI (§16, §28) -------------------------------------------------------
    #: "extractive" (no key, always works) or "anthropic". §16's provider choice is
    #: configuration, never a code change.
    ai_provider: str = "extractive"
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"
    #: Generate presentation text for stories at or above this importance, so the money
    #: goes to what matters (§28 "process only important articles").
    ai_min_importance: float = 0.0
    #: Spend caps in USD. Zero disables the cap. Checked before every call.
    ai_daily_cost_limit_usd: float = 5.0
    ai_monthly_cost_limit_usd: float = 100.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
