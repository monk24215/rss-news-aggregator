"""Shared fixtures.

Most of the suite runs anywhere with no services. The schema, pipeline, and web tests
are different: the guarantees they check — the immutability trigger, CHECK constraints,
partial unique indexes, real query behaviour — live in Postgres, and testing them
against SQLite would prove nothing about production. Those use the `db` fixture, which
skips cleanly when no database is reachable and runs for real in CI.

**The suite runs against its own database.** Point `TEST_DATABASE_URL` at one, or the
default is `<your database>_test`, created automatically. Sharing a database with
development data caused real failures once seeded rows existed: a fixture creating the
tag `power-grid` collided with the seeded one. A test suite that only passes on an empty
database is a test suite that will fail on someone's laptop.

Every test using `db` runs inside a transaction that is rolled back afterwards, so tests
cannot affect each other either.
"""

from __future__ import annotations  # noqa: I001

import os

# Redirect the application's database URL BEFORE any app module reads it. `app.config`
# caches settings and `app.db` builds its engine at import time, so this has to happen
# first — which is why it sits above the imports rather than in a fixture.
def _test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
    )
    head, _, name = base.rpartition("/")
    name = (name or "postgres").split("?")[0]
    return f"{head}/{name}_test" if not name.endswith("_test") else base


TEST_URL = _test_database_url()
os.environ["DATABASE_URL"] = TEST_URL

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SYNC_DATABASE_URL, engine  # noqa: E402
from app.models import Base  # noqa: E402


def _ensure_database_exists() -> bool:
    """Create the test database if it is missing. Returns False if Postgres is absent."""
    head, _, name = SYNC_DATABASE_URL.rpartition("/")
    admin_url = f"{head}/postgres"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        admin.dispose()
        return True
    except Exception:
        return False


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
    if not _ensure_database_exists() or not _database_available():
        pytest.skip("no database reachable; schema tests need Postgres")

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", SYNC_DATABASE_URL)
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
