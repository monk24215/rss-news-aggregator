"""The administrative interface — §35, §29.3, §37, §45.

§35.1 is explicit about the ordering: what needs attention first, then what happened
today, then the operational detail. A dashboard that opens with a wall of counters makes
the operator do the triage the system should have done for them.

Read-mostly for now. The actions that exist are the reversible ones — hide a story,
requeue a source, resolve a review item — and each writes to the audit log (§37) and
change history (§36) so it can be explained and undone.

There is no authentication yet: §38's roles exist in the schema and Section 3 wires
them up. Until then this is mounted under /admin and protected the way any unreleased
admin surface is — by not publishing the URL, and by Railway's own access controls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    AIResult,
    AIUsage,
    Article,
    AuditLog,
    ChangeHistory,
    FeedInstance,
    ProcessingJob,
    ReviewQueueItem,
    Source,
    SourceFetchLog,
    Story,
)
from app.services import presentation
from app.services.ai import spend_since

router = APIRouter(prefix="/admin")


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    context.setdefault("now", datetime.now(timezone.utc))
    context.setdefault("relative_time", presentation.relative_time)
    return request.app.state.templates.TemplateResponse(request, f"admin/{template}", context)


def _audit(
    session: Session,
    action: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
    *,
    field: str | None = None,
    previous=None,
    new=None,
) -> None:
    """Record an administrative change so it is traceable (§37) and undoable (§36)."""
    now = datetime.now(timezone.utc)
    change_id = None
    if field is not None and entity_id is not None:
        change = ChangeHistory(
            occurred_at=now,
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            previous_value={"value": previous},
            new_value={"value": new},
        )
        session.add(change)
        session.flush()
        change_id = change.id
    session.add(
        AuditLog(
            occurred_at=now,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            change_id=change_id,
        )
    )
    session.flush()


# --- Dashboard ---------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """§35.1 — needs attention, then today, then the numbers."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    failing = list(
        session.execute(
            select(Source)
            .where(Source.consecutive_failures > 0, Source.is_active.is_(True))
            .order_by(Source.consecutive_failures.desc())
        )
        .scalars()
        .all()
    )
    review_items = list(
        session.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.state == "pending_review")
            .order_by(ReviewQueueItem.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    conflicted = list(
        session.execute(
            select(Story)
            .where(Story.has_unresolved_conflicts.is_(True), Story.is_hidden.is_(False))
            .order_by(Story.last_updated_at.desc().nullslast())
            .limit(10)
        )
        .scalars()
        .all()
    )
    failed_jobs = list(
        session.execute(
            select(ProcessingJob)
            .where(ProcessingJob.status == "failed", ProcessingJob.created_at >= day_ago)
            .order_by(ProcessingJob.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    empty_feeds = list(
        session.execute(
            select(FeedInstance)
            .where(FeedInstance.is_active.is_(True), FeedInstance.story_count == 0)
            .order_by(FeedInstance.name)
        )
        .scalars()
        .all()
    )

    def _count(model, *where) -> int:
        return int(
            session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()
        )

    today = {
        "articles_collected": _count(Article, Article.created_at >= day_ago),
        "articles_processed": _count(
            Article, Article.created_at >= day_ago, Article.story_id.is_not(None)
        ),
        "stories_created": _count(Story, Story.created_at >= day_ago),
        "ai_generations": _count(AIResult, AIResult.created_at >= day_ago),
        "ai_failures": _count(
            AIUsage, AIUsage.occurred_at >= day_ago, AIUsage.succeeded.is_(False)
        ),
    }

    trending = list(
        session.execute(
            select(Story)
            .where(Story.is_hidden.is_(False))
            .order_by(Story.trending_score.desc().nullslast())
            .limit(8)
        )
        .scalars()
        .all()
    )

    return _render(
        request,
        "dashboard.html",
        {
            "needs_attention": {
                "failing_sources": failing,
                "review_items": review_items,
                "conflicted_stories": conflicted,
                "failed_jobs": failed_jobs,
                "empty_feeds": empty_feeds,
            },
            "attention_total": (
                len(failing) + len(review_items) + len(conflicted) + len(failed_jobs)
                + len(empty_feeds)
            ),
            "today": today,
            "totals": {
                "sources": _count(Source),
                "active_sources": _count(Source, Source.is_active.is_(True)),
                "articles": _count(Article),
                "stories": _count(Story),
                "feeds": _count(FeedInstance),
                "queue_pending": _count(ProcessingJob, ProcessingJob.status == "pending"),
            },
            "ai_spend": {
                "day": spend_since(session, day_ago),
                "month": spend_since(session, now - timedelta(days=30)),
            },
            "trending": trending,
        },
    )


# --- Sources (§35.3) ---------------------------------------------------------


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.execute(
        select(Source, func.count(Article.id))
        .join(Article, Article.source_id == Source.id, isouter=True)
        .group_by(Source.id)
        .order_by(Source.consecutive_failures.desc(), Source.name)
    ).all()
    return _render(request, "sources.html", {"rows": rows})


@router.get("/source/{source_id}", response_class=HTMLResponse)
def source_detail(
    request: Request, source_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    logs = list(
        session.execute(
            select(SourceFetchLog)
            .where(SourceFetchLog.source_id == source_id)
            .order_by(SourceFetchLog.attempted_at.desc())
            .limit(25)
        )
        .scalars()
        .all()
    )
    return _render(request, "source_detail.html", {"source": source, "logs": logs})


@router.post("/source/{source_id}/toggle")
def toggle_source(
    source_id: int, session: Session = Depends(get_session)
) -> RedirectResponse:
    """Activate or deactivate a source (§35.4)."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    previous = source.is_active
    source.is_active = not previous
    _audit(
        session,
        "source.toggle",
        "source",
        source.id,
        f"{'Deactivated' if previous else 'Activated'} {source.name}",
        field="is_active",
        previous=previous,
        new=source.is_active,
    )
    session.commit()
    return RedirectResponse(f"/admin/source/{source_id}", status_code=303)


@router.post("/source/{source_id}/clear-failures")
def clear_failures(source_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    """Lift a source's rest period so the next scheduler pass retries it (§12)."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    previous = source.consecutive_failures
    source.consecutive_failures = 0
    source.disabled_until = None
    _audit(
        session,
        "source.clear_failures",
        "source",
        source.id,
        f"Cleared {previous} consecutive failures on {source.name}",
        field="consecutive_failures",
        previous=previous,
        new=0,
    )
    session.commit()
    return RedirectResponse(f"/admin/source/{source_id}", status_code=303)


# --- Review queue (§27.2) ----------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
def review(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """One screen for every decision awaiting a human (§27.2)."""
    items = list(
        session.execute(
            select(ReviewQueueItem)
            .where(ReviewQueueItem.state == "pending_review")
            .order_by(ReviewQueueItem.confidence.asc().nullsfirst(), ReviewQueueItem.created_at)
            .limit(100)
        )
        .scalars()
        .all()
    )
    cards = {}
    for item in items:
        if item.story_id and item.story_id not in cards:
            story = session.get(Story, item.story_id)
            if story is not None:
                cards[item.story_id] = presentation.build_card(session, story)
    return _render(request, "review.html", {"items": items, "cards": cards})


@router.post("/review/{item_id}/resolve")
def resolve_review(
    item_id: int,
    decision: str = Form("approved"),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    item = session.get(ReviewQueueItem, item_id)
    if item is None:
        raise HTTPException(404, "Review item not found")
    item.state = "approved" if decision == "approved" else "rejected"
    item.resolved_at = datetime.now(timezone.utc)
    _audit(
        session,
        "review.resolve",
        "review_queue_item",
        item.id,
        f"Review item {item.id} ({item.reason}) marked {item.state}",
        field="state",
        previous="pending_review",
        new=item.state,
    )
    session.commit()
    return RedirectResponse("/admin/review", status_code=303)


# --- Stories and feeds -------------------------------------------------------


@router.get("/stories", response_class=HTMLResponse)
def stories(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    rows = list(
        session.execute(
            select(Story).order_by(Story.last_updated_at.desc().nullslast()).limit(60)
        )
        .scalars()
        .all()
    )
    return _render(
        request,
        "stories.html",
        {"cards": [presentation.build_card(session, story) for story in rows]},
    )


@router.post("/story/{story_id}/hide")
def hide_story(story_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    """§35.4 — hide a story, and record it so it can be undone (§36)."""
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(404, "Story not found")
    previous = story.is_hidden
    story.is_hidden = not previous
    story.has_manual_override = True
    _audit(
        session,
        "story.hide",
        "story",
        story.id,
        f"{'Unhid' if previous else 'Hid'} story {story.id}",
        field="is_hidden",
        previous=previous,
        new=story.is_hidden,
    )
    session.commit()
    return RedirectResponse("/admin/stories", status_code=303)


@router.post("/story/{story_id}/lock")
def lock_story(story_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    """§19 — stop the clusterer touching a story a human has settled."""
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(404, "Story not found")
    previous = story.is_locked
    story.is_locked = not previous
    story.has_manual_override = True
    _audit(
        session,
        "story.lock",
        "story",
        story.id,
        f"{'Unlocked' if previous else 'Locked'} story {story.id} against automatic clustering",
        field="is_locked",
        previous=previous,
        new=story.is_locked,
    )
    session.commit()
    return RedirectResponse("/admin/stories", status_code=303)


@router.get("/feeds", response_class=HTMLResponse)
def feeds(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """§29.3 — configuration next to what it actually produces."""
    rows = list(session.execute(select(FeedInstance).order_by(FeedInstance.name)).scalars().all())
    from app.services.feeds import feed_stories, load_rules

    detail = []
    for feed in rows:
        stories_in_feed = feed_stories(session, feed, limit=5)
        detail.append(
            {
                "feed": feed,
                "rules": load_rules(session, feed),
                "sample": [presentation.build_card(session, s) for s in stories_in_feed],
            }
        )
    return _render(request, "feeds.html", {"feeds": detail})


# --- Audit trail (§37) -------------------------------------------------------


@router.get("/audit", response_class=HTMLResponse)
def audit(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    entries = list(
        session.execute(select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(100))
        .scalars()
        .all()
    )
    return _render(request, "audit.html", {"entries": entries})
