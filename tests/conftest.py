"""Shared fixtures.

Most of the suite runs anywhere with no services. The schema tests are different: the
guarantees they check — the immutability trigger, CHECK constraints, partial unique
indexes — live in Postgres, and testing them against SQLite would prove nothing about
production. Those tests use the `db` fixture, which skips cleanly when no database is
reachable and runs for real in CI, where service containers are present.

Every test using `db` runs inside a transaction that is rolled back afterwards, so the
suite leaves no residue and tests cannot affect each other.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SYNC_DATABASE_URL, engine
from app.models import Base


def _database_available() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    return SYNC_DATABASE_URL


@pytest.fixture(scope="session")
def schema():
    """Ensure the schema exists, including triggers.

    Migrations own the triggers, so we run Alembic rather than `create_all` — testing a
    schema the migrations did not build would let the two drift apart silently.
    """
    if not _database_available():
        pytest.skip("no database reachable; schema tests need Postgres")
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield Base.metadata


@pytest.fixture
def db(schema):
    """A session wrapped in a transaction that is always rolled back."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        # A test that provoked a constraint violation may already have rolled back.
        if transaction.is_active:
            transaction.rollback()
        connection.close()
