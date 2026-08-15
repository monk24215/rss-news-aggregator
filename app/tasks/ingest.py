"""Ingestion tasks — the queue-facing wrappers around `app.services.ingest`.

Each task is thin on purpose. The logic lives in services, so a failed AI stage later
can be retried without re-fetching (Non-Negotiable #8), and so the pipeline can be
tested without Redis.

Retry safety (§14): `fetch_source` is safe to run twice — the second run finds every
item already present and reports duplicates instead of inserting them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.db import SessionLocal
from app.models import ProcessingJob, Source
from app.queue import QUEUE_WORKER
from app.services.ingest import due_sources, ingest_source

logger = logging.getLogger("worker.ingest")


async def fetch_source(ctx: dict, source_id: int) -> str:
    """Fetch one source and store new items.

    Records a `processing_jobs` row for the `fetch` stage so §14's per-stage visibility
    is real from the first feature, not retrofitted.
    """
    now = datetime.now(timezone.utc)
    session = SessionLocal()
    job = ProcessingJob(
        stage="fetch",
        status="running",
        source_id=source_id,
        started_at=now,
        attempts=1,
        queue_job_id=str(ctx.get("job_id") or "")[:64] or None,
    )
    session.add(job)
    session.commit()

    try:
        source = session.get(Source, source_id)
        if source is None:
            job.status = "failed"
            job.error = "source not found"
            job.finished_at = datetime.now(timezone.utc)
            session.commit()
            return f"source {source_id} not found"

        with httpx.Client() as client:
            report = ingest_source(session, source, client=client, now=now)

        job.status = "succeeded" if report.ok else "failed"
        job.error = report.error
        job.finished_at = datetime.now(timezone.utc)
        job.duration_ms = report.response_ms
        job.payload = {
            "items_seen": report.items_seen,
            "items_new": report.items_new,
            "items_duplicate": report.items_duplicate,
            "not_modified": report.not_modified,
        }
        session.commit()

        logger.info(report.summary())

        # Hand the new articles to the next stage. Enqueueing per article keeps each
        # stage independently retryable rather than coupling a whole fetch to one job.
        redis = ctx.get("redis")
        if redis is not None and report.new_article_ids:
            for article_id in report.new_article_ids:
                await redis.enqueue_job("process_article", article_id, _queue_name=QUEUE_WORKER)

        return report.summary()
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"[:500]
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        logger.exception("fetch_source failed for %s", source_id)
        raise
    finally:
        session.close()


async def enqueue_due_sources(ctx: dict) -> str:
    """Scheduler cron: find sources whose interval has elapsed and queue a fetch each.

    The scheduler never fetches (§43.2) — it only decides what should happen next.
    """
    session = SessionLocal()
    try:
        ids = due_sources(session)
    finally:
        session.close()

    redis = ctx["redis"]
    for source_id in ids:
        await redis.enqueue_job("fetch_source", source_id, _queue_name=QUEUE_WORKER)
    logger.info("enqueued %s source fetches", len(ids))
    return f"enqueued {len(ids)}"
