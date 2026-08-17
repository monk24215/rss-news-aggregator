"""Public reader routes — §34, §30, §31.

Every handler here is a small number of indexed reads and a template render. Nothing
fetches, clusters, scores, or calls a model on the request path (Non-Negotiable #7) —
by the time a reader arrives, the work is already done and sitting in
`feed_instance_entries`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    Article,
    FeedInstance,
    Source,
    Story,
    StoryTag,
    Tag,
)
from app.services import presentation, rss
from app.services.feeds import feed_stories
from app.services.scoring import explain

router = APIRouter()

#: The feed the front page shows when no other is chosen. Created by `scripts/seed.py`.
DEFAULT_FEED_SLUG = "top-stories"


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _default_feed(session: Session) -> FeedInstance | None:
    feed = session.execute(
        select(FeedInstance).where(FeedInstance.slug == DEFAULT_FEED_SLUG)
    ).scalar_one_or_none()
    if feed is not None:
        return feed
    return session.execute(
        select(FeedInstance).where(FeedInstance.is_active.is_(True)).order_by(FeedInstance.id)
    ).scalars().first()


def _cards(session: Session, stories: list[Story]) -> list[presentation.StoryCard]:
    return [presentation.build_card(session, story) for story in stories]


def _templates(request: Request):
    return request.app.state.templates


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    context.setdefault("now", datetime.now(timezone.utc))
    context.setdefault("relative_time", presentation.relative_time)
    return _templates(request).TemplateResponse(request, template, context)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """Trending and latest — the front page (§34.2)."""
    feed = _default_feed(session)
    trending: list[Story] = []
    latest: list[Story] = []

    if feed is not None:
        stories = feed_stories(session, feed, limit=60)
        trending = sorted(stories, key=lambda s: s.trending_score or 0, reverse=True)[:6]
        trending_ids = {s.id for s in trending}
        latest = [s for s in stories if s.id not in trending_ids][:24]
    else:
        latest = list(
            session.execute(
                select(Story)
                .where(Story.is_hidden.is_(False))
                .order_by(Story.last_updated_at.desc().nullslast())
                .limit(24)
            )
            .scalars()
            .all()
        )

    return _render(
        request,
        "index.html",
        {
            "feed": feed,
            "trending": _cards(session, trending),
            "latest": _cards(session, latest),
            "feeds": _all_feeds(session),
        },
    )


@router.get("/story/{slug}", response_class=HTMLResponse)
def story_page(
    request: Request, slug: str, session: Session = Depends(get_session)
) -> HTMLResponse:
    """One story: what happened, who reported it, and the reporting itself (§34.3)."""
    story = session.execute(
        select(Story).where(or_(Story.slug == slug, Story.id == _maybe_int(slug)))
    ).scalar_one_or_none()
    if story is None or story.is_hidden:
        raise HTTPException(status_code=404, detail="Story not found")

    card = presentation.build_card(session, story)
    return _render(
        request,
        "story.html",
        {
            "card": card,
            "story": story,
            "timeline": presentation.timeline_for(session, story),
            "why_trending": explain(session, story, "trending"),
            "related": _cards(session, _related_stories(session, story)),
            "feeds": _all_feeds(session),
        },
    )


@router.get("/topics", response_class=HTMLResponse)
def topics(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """Browse by tag (§34.2)."""
    rows = session.execute(
        select(Tag, func.count(StoryTag.story_id))
        .join(StoryTag, StoryTag.tag_id == Tag.id, isouter=True)
        .where(Tag.is_active.is_(True))
        .group_by(Tag.id)
        .order_by(func.count(StoryTag.story_id).desc(), Tag.name)
    ).all()
    return _render(
        request,
        "topics.html",
        {"topics": [(tag, count) for tag, count in rows], "feeds": _all_feeds(session)},
    )


@router.get("/topic/{slug}", response_class=HTMLResponse)
def topic(request: Request, slug: str, session: Session = Depends(get_session)) -> HTMLResponse:
    tag = session.execute(select(Tag).where(Tag.slug == slug)).scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    stories = list(
        session.execute(
            select(Story)
            .join(StoryTag, StoryTag.story_id == Story.id)
            .where(StoryTag.tag_id == tag.id, Story.is_hidden.is_(False))
            .order_by(Story.last_updated_at.desc().nullslast())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return _render(
        request,
        "list.html",
        {
            "page_title": tag.name,
            "page_subtitle": tag.description or f"Stories tagged {tag.name}",
            "cards": _cards(session, stories),
            "feeds": _all_feeds(session),
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """Browse by publication (§34.2), with the attribution §34.6 requires."""
    rows = session.execute(
        select(Source, func.count(Article.id))
        .join(Article, Article.source_id == Source.id, isouter=True)
        .where(Source.is_hidden.is_(False))
        .group_by(Source.id)
        .order_by(func.count(Article.id).desc(), Source.name)
    ).all()
    return _render(
        request,
        "sources.html",
        {"sources": [(source, count) for source, count in rows], "feeds": _all_feeds(session)},
    )


@router.get("/feed/{slug}", response_class=HTMLResponse)
def feed_page(request: Request, slug: str, session: Session = Depends(get_session)) -> HTMLResponse:
    feed = session.execute(
        select(FeedInstance).where(FeedInstance.slug == slug)
    ).scalar_one_or_none()
    if feed is None or not feed.is_active:
        raise HTTPException(status_code=404, detail="Feed not found")
    stories = feed_stories(session, feed)
    return _render(
        request,
        "list.html",
        {
            "page_title": feed.name,
            "page_subtitle": feed.description,
            "cards": _cards(session, stories),
            "rss_slug": feed.slug if feed.rss_enabled else None,
            "feeds": _all_feeds(session),
        },
    )


@router.get("/rss/{slug}")
def feed_rss(slug: str, request: Request, session: Session = Depends(get_session)) -> Response:
    """A feed instance as RSS — /rss/power-grid (§30)."""
    feed = session.execute(
        select(FeedInstance).where(FeedInstance.slug == slug)
    ).scalar_one_or_none()
    if feed is None or not feed.is_active or not feed.rss_enabled:
        raise HTTPException(status_code=404, detail="Feed not found")
    xml = rss.render_feed(session, feed, base_url=_base_url(request))
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query("", max_length=200),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Search headlines and summaries (§31).

    Deliberately a plain SQL `ILIKE` for v1. §31 asks that the data layer allow semantic
    search to be added later, not that v1 ship it — and an honest keyword search beats a
    half-built vector index.
    """
    cards: list[presentation.StoryCard] = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        stories = list(
            session.execute(
                select(Story)
                .join(Article, Article.story_id == Story.id)
                .where(
                    Story.is_hidden.is_(False),
                    or_(
                        Article.original_headline.ilike(pattern),
                        Article.original_description.ilike(pattern),
                    ),
                )
                .order_by(Story.last_updated_at.desc().nullslast())
                .limit(50)
            )
            .scalars()
            .unique()
            .all()
        )
        cards = _cards(session, stories)
    return _render(
        request,
        "list.html",
        {
            "page_title": f"Search: {q}" if q else "Search",
            "page_subtitle": f"{len(cards)} result{'s' if len(cards) != 1 else ''}" if q else None,
            "cards": cards,
            "search_query": q,
            "feeds": _all_feeds(session),
        },
    )


def _all_feeds(session: Session) -> list[FeedInstance]:
    return list(
        session.execute(
            select(FeedInstance).where(FeedInstance.is_active.is_(True)).order_by(FeedInstance.name)
        )
        .scalars()
        .all()
    )


def _related_stories(session: Session, story: Story, limit: int = 4) -> list[Story]:
    """Stories sharing a tag, most recent first (§34.3)."""
    tag_ids = list(
        session.execute(select(StoryTag.tag_id).where(StoryTag.story_id == story.id))
        .scalars()
        .all()
    )
    if not tag_ids:
        return []
    return list(
        session.execute(
            select(Story)
            .join(StoryTag, StoryTag.story_id == Story.id)
            .where(
                StoryTag.tag_id.in_(tag_ids),
                Story.id != story.id,
                Story.is_hidden.is_(False),
            )
            .order_by(Story.last_updated_at.desc().nullslast())
            .limit(limit)
        )
        .scalars()
        .unique()
        .all()
    )


def _maybe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
