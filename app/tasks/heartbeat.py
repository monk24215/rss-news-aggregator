"""Heartbeat task.

The Section 1 proof: a trivial job the scheduler enqueues on a timer and the worker
executes. Writing a row demonstrates the full scheduler → Redis → worker → Postgres path
end to end, BEFORE any real ingestion is built on it.

Retry-safe (Non-Negotiable #8): running it twice only inserts an extra tick row, which
is harmless. Nothing here depends on prior state.
"""

import logging

from app.db import SessionLocal
from app.models import Heartbeat

logger = logging.getLogger("worker.heartbeat")


async def heartbeat(ctx: dict) -> str:
    """Insert a heartbeat row and return a short status string."""
    session = SessionLocal()
    try:
        tick = Heartbeat(source="scheduler")
        session.add(tick)
        session.commit()
        session.refresh(tick)
        logger.info("heartbeat tick id=%s at %s", tick.id, tick.created_at.isoformat())
        return f"heartbeat:{tick.id}"
    finally:
        session.close()
