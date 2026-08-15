"""Config tests — settings load and URL normalization behaves."""

from app.config import Settings
from app.db import _normalize_sync_url


def test_settings_defaults():
    s = Settings()
    assert s.redis_url.startswith("redis://")
    assert s.heartbeat_interval_seconds >= 1


def test_normalize_postgres_scheme():
    assert _normalize_sync_url("postgres://u:p@h:5432/db").startswith(
        "postgresql+psycopg://"
    )
    assert _normalize_sync_url("postgresql://u:p@h:5432/db").startswith(
        "postgresql+psycopg://"
    )
    # Already-correct URLs pass through unchanged.
    already = "postgresql+psycopg://u:p@h:5432/db"
    assert _normalize_sync_url(already) == already
