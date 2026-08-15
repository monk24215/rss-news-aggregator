"""AI tasks — §17, §18, §20, §28.

Generation is its own stage, enqueued after a story is scored, so a provider outage or a
budget stop costs the presentation text and nothing else: the story, its sources, and
its place in the feeds are already saved (Non-Negotiable #8).

`ai_min_importance` gates spending on what matters (§28). With the extractive provider
the gate is irrelevant — it is free — but the pipeline should not have to know which
provider is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.db import SessionLocal
from app.models import ProcessingJob, Story
from app.services.ai import build_provider, enrich_story
from app.services.feeds import publish_story

logger = logging.getLogger("worker.ai")


async def generate_story_text(ctx: dict, story_id: int, force: bool = False) -> str:
    """Generate the headline and summary/synthesis for one story."""
    settings = get_settings()
    session = SessionLocal()
    now = datetime.now(timezone.utc)
    job = ProcessingJob(
        stage="generate_ai",
        status="running",
        story_id=story_id,
        started_at=now,
        attempts=1,
        queue_job_id=str(ctx.get("job_id") or "")[:64] or None,
    )
    session.add(job)
    session.commit()

    try:
        story = session.get(Story, story_id)
        if story is None:
            job.status = "failed"
            job.error = "story not found"
            job.finished_at = datetime.now(timezone.utc)
            session.commit()
            return f"story {story_id} not found"

        threshold = settings.ai_min_importance or 0
        if (story.importance_score or 0) < threshold:
            job.status = "succeeded"
            job.payload = {"skipped": "below ai_min_importance"}
            job.finished_at = datetime.now(timezone.utc)
            session.commit()
            return f"story {story_id} below importance threshold"

        outcomes = enrich_story(session, story, provider=build_provider(settings), now=now)

        # The presentation changed, so the feed entries that quote it are stale.
        publish_story(session, story, now=now)

        job.status = "succeeded"
        job.payload = {
            operation: {
                "ok": outcome.ok,
                "reused": outcome.reused,
                "provider": outcome.result.provider if outcome.result else None,
                "confidence": outcome.result.confidence if outcome.result else None,
            }
            for operation, outcome in outcomes.items()
        }
        job.finished_at = datetime.now(timezone.utc)
        session.commit()

        summary = ", ".join(
            f"{op}={'reused' if o.reused else (o.result.provider if o.result else 'failed')}"
            for op, o in outcomes.items()
        )
        logger.info("story %s: %s", story_id, summary)
        return f"story {story_id}: {summary}"
    except Exception as exc:
        session.rollback()
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"[:500]
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        logger.exception("generate_story_text failed for %s", story_id)
        raise
    finally:
        session.close()
