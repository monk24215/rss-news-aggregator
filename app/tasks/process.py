"""Per-article processing stages — §11 steps 10-16, §14.

Ingestion stops at "the article exists". Everything after runs here, one stage at a
time, each recorded in `processing_jobs`, so a failure in any stage is retried on its
own without re-fetching the source (Non-Negotiable #8).

    tag → extract entities → find or open a story → score → publish to feeds

Every stage is idempotent. Running this twice on the same article converges on the same
rows rather than duplicating work — which is what makes "retry" a safe instruction
rather than a gamble.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import Article, ProcessingJob, ReviewQueueItem, Story
from app.services.cluster import assign_story
from app.services.enrich import apply_tags
from app.services.feeds import publish_story
from app.services.scoring import rescore

logger = logging.getLogger("worker.process")

#: The stages this task runs, in order. Named so `processing_jobs` reads like the
#: pipeline in §14 rather than like an implementation detail.
STAGES = ("tag", "cluster", "score", "publish")


def _job(session, stage: str, article_id: int, ctx: dict) -> ProcessingJob:
    job = ProcessingJob(
        stage=stage,
        status="running",
        article_id=article_id,
        started_at=datetime.now(timezone.utc),
        attempts=1,
        queue_job_id=str(ctx.get("job_id") or "")[:64] or None,
    )
    session.add(job)
    session.flush()
    return job


def _finish(
    session,
    job: ProcessingJob,
    ok: bool,
    error: str | None = None,
    payload: dict | None = None,
) -> None:
    job.status = "succeeded" if ok else "failed"
    job.error = error
    job.payload = payload
    job.finished_at = datetime.now(timezone.utc)
    if job.started_at:
        job.duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)
    session.flush()


async def process_article(ctx: dict, article_id: int) -> str:
    """Run tagging, clustering, scoring, and feed placement for one article."""
    session = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        article = session.get(Article, article_id)
        if article is None:
            logger.warning("article %s not found", article_id)
            return f"article {article_id} not found"

        if article.ai_processing_disabled:
            # An editor has taken this one out of automation (§35.4).
            logger.info("article %s skipped: processing disabled by an editor", article_id)
            return f"article {article_id} skipped"

        # --- tag ---
        job = _job(session, "tag", article_id, ctx)
        added = apply_tags(session, article)
        _finish(session, job, True, payload={"tags_added": added})

        # --- cluster ---
        job = _job(session, "cluster", article_id, ctx)
        decision = assign_story(session, article, now=now)
        _finish(
            session,
            job,
            True,
            payload={
                "story_id": decision.story_id,
                "created": decision.created,
                "confidence": decision.confidence,
                "needs_review": decision.needs_review,
                "rationale": decision.rationale,
            },
        )

        # §27.2 — a borderline merge is acted on but put in front of a human.
        if decision.needs_review and decision.story_id:
            session.add(
                ReviewQueueItem(
                    reason="story_merge",
                    article_id=article_id,
                    story_id=decision.story_id,
                    confidence=decision.confidence,
                    detail={"rationale": decision.rationale},
                )
            )
            session.flush()

        story = session.get(Story, decision.story_id) if decision.story_id else None
        if story is None:
            session.commit()
            return f"article {article_id}: no story"

        # --- score ---
        job = _job(session, "score", article_id, ctx)
        importance, trending = rescore(session, story, now=now)
        _finish(
            session, job, True, payload={"importance": importance, "trending": trending}
        )

        # --- publish ---
        job = _job(session, "publish", article_id, ctx)
        placed = publish_story(session, story, now=now)
        _finish(session, job, True, payload={"feeds": [feed_id for feed_id, _ in placed]})

        # §17/§18 run as their own stage: a provider outage must not cost us the
        # story, only its presentation text.
        redis = ctx.get("redis")
        if redis is not None:
            from app.queue import QUEUE_WORKER

            await redis.enqueue_job("generate_story_text", story.id, _queue_name=QUEUE_WORKER)

        session.commit()
        result = (
            f"article {article_id} → story {story.id} "
            f"({'new' if decision.created else 'joined'}, conf {decision.confidence:.2f}), "
            f"importance {importance:.0f}, trending {trending:.0f}, feeds {len(placed)}"
        )
        logger.info(result)
        return result
    except Exception as exc:
        session.rollback()
        logger.exception("process_article failed for %s", article_id)
        failed = _job(session, "publish", article_id, ctx)
        _finish(session, failed, False, error=f"{type(exc).__name__}: {exc}"[:500])
        session.commit()
        raise
    finally:
        session.close()


async def reprocess_story(ctx: dict, story_id: int) -> str:
    """Re-score and re-publish one story (§35.4 'reprocess a story').

    Separate from `process_article` because an administrator changing a tag or a feed's
    rules should not require re-fetching anything.
    """
    session = SessionLocal()
    try:
        story = session.get(Story, story_id)
        if story is None:
            return f"story {story_id} not found"
        importance, trending = rescore(session, story)
        placed = publish_story(session, story)
        session.commit()
        return (
            f"story {story_id}: importance {importance:.0f}, "
            f"trending {trending:.0f}, feeds {len(placed)}"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
