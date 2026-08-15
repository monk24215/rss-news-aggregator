"""Per-article processing stages — §14.

Ingestion stops at "the article exists". Everything after that (tagging, clustering,
scoring, AI) runs here, one stage at a time, so a failure in any of them is retried on
its own without re-fetching the source (Non-Negotiable #8).

In Group A this is the seam: `fetch_source` hands new article ids over, and the stages
are filled in behind this entry point as they are built.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import Article, ProcessingJob

logger = logging.getLogger("worker.process")


async def process_article(ctx: dict, article_id: int) -> str:
    """Run the post-ingest stages for one article.

    Retry-safe: every stage it will contain is written to be idempotent, so running this
    twice converges on the same row rather than duplicating work.
    """
    session = SessionLocal()
    started = datetime.now(timezone.utc)
    job = ProcessingJob(
        stage="normalize",
        status="running",
        article_id=article_id,
        started_at=started,
        attempts=1,
        queue_job_id=str(ctx.get("job_id") or "")[:64] or None,
    )
    session.add(job)
    session.commit()
    try:
        article = session.get(Article, article_id)
        if article is None:
            job.status = "failed"
            job.error = "article not found"
            job.finished_at = datetime.now(timezone.utc)
            session.commit()
            return f"article {article_id} not found"

        # Normalization already happened at ingest; recording the stage as succeeded
        # keeps the job table an honest account of what ran.
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        job.duration_ms = int((job.finished_at - started).total_seconds() * 1000)
        session.commit()
        logger.info("processed article %s", article_id)
        return f"processed {article_id}"
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"[:500]
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        raise
    finally:
        session.close()
