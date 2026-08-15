"""Database engine and session management.

PostgreSQL is the canonical store (§43.3). This module provides a synchronous engine
used by the worker and by migrations, plus a helper to check connectivity for /health.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _normalize_sync_url(url: str) -> str:
    """Railway/Heroku often provide 'postgres://'. SQLAlchemy 2 + psycopg wants
    'postgresql+psycopg://'. Normalize so either form works."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


settings = get_settings()
SYNC_DATABASE_URL = _normalize_sync_url(settings.database_url)

engine = create_engine(SYNC_DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def check_db() -> bool:
    """Return True if a trivial query against Postgres succeeds."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
