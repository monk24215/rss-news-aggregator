"""What the reader sees — §34, §30.

The rule this module exists to enforce is Non-Negotiable #3: AI-generated text must
always be distinguishable from original reporting. Rather than leave that to whoever
writes the next template, every headline and summary the site renders comes from here
as a `Rendered` object that says which it is. A template can then label it, and a
missing label is visible in one place instead of scattered across pages.

When no AI result exists — which is every story until a key is configured — the reader
still gets a headline and a summary: the publisher's own. `is_ai` is False, nothing is
labelled as generated, and the page is honest with zero AI calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIResult, Article, Source, Story, StoryTag, StoryTimelineEvent, Tag


@dataclass(frozen=True)
class Rendered:
    """A piece of display text and its provenance."""

    text: str
    is_ai: bool
    #: How many source reports an AI artifact was generated from (§34.5).
    source_count: int = 0
    was_edited: bool = False

    @property
    def attribution(self) -> str:
        if not self.is_ai:
            return "Original reporting"
        if self.was_edited:
            return "AI summary, edited"
        if self.source_count:
            return f"AI summary · generated from {self.source_count} source reports"
        return "AI summary"


def _current(session: Session, story_id: int, operation: str) -> AIResult | None:
    return session.execute(
        select(AIResult).where(
            AIResult.story_id == story_id,
            AIResult.operation == operation,
            AIResult.is_current.is_(True),
        )
    ).scalar_one_or_none()


def story_articles(session: Session, story: Story) -> list[Article]:
    """The story's source articles, oldest first — the reporting underneath (§34.1)."""
    return list(
        session.execute(
            select(Article)
            .where(Article.story_id == story.id, Article.is_hidden.is_(False))
            .order_by(Article.original_published_at.asc().nullslast())
        )
        .scalars()
        .all()
    )


def headline(session: Session, story: Story, articles: list[Article] | None = None) -> Rendered:
    """The story's headline — AI when available, otherwise the first report's (§17)."""
    result = _current(session, story.id, "generate_headline")
    if result and result.display_content:
        return Rendered(
            result.display_content,
            is_ai=True,
            source_count=len(result.inputs),
            was_edited=bool(result.edited_content),
        )
    articles = articles if articles is not None else story_articles(session, story)
    if articles:
        return Rendered(articles[0].original_headline, is_ai=False)
    return Rendered("Untitled story", is_ai=False)


def summary(
    session: Session, story: Story, articles: list[Article] | None = None
) -> Rendered | None:
    """The story's summary — AI when available, otherwise the first report's own (§18)."""
    result = _current(session, story.id, "generate_summary") or _current(
        session, story.id, "synthesize_story"
    )
    if result and result.display_content:
        return Rendered(
            result.display_content,
            is_ai=True,
            source_count=len(result.inputs),
            was_edited=bool(result.edited_content),
        )
    articles = articles if articles is not None else story_articles(session, story)
    for article in articles:
        if article.original_description:
            return Rendered(article.original_description, is_ai=False)
    return None


def sources_for(session: Session, story: Story) -> list[Source]:
    """Distinct publications covering a story, for the "Reuters · AP · …" line (§34.5)."""
    source_ids = set(
        session.execute(select(Article.source_id).where(Article.story_id == story.id))
        .scalars()
        .all()
    )
    if not source_ids:
        return []
    return list(
        session.execute(select(Source).where(Source.id.in_(source_ids)).order_by(Source.name))
        .scalars()
        .all()
    )


def tags_for(session: Session, story: Story) -> list[Tag]:
    return list(
        session.execute(
            select(Tag)
            .join(StoryTag, StoryTag.tag_id == Tag.id)
            .where(StoryTag.story_id == story.id)
            .order_by(Tag.name)
        )
        .scalars()
        .all()
    )


def timeline_for(session: Session, story: Story) -> list[StoryTimelineEvent]:
    return list(
        session.execute(
            select(StoryTimelineEvent)
            .where(StoryTimelineEvent.story_id == story.id)
            .order_by(StoryTimelineEvent.occurred_at.asc())
        )
        .scalars()
        .all()
    )


def first_report(session: Session, story: Story) -> Article | None:
    """The article that opened the story — powers "First reported by…" (§34.3)."""
    articles = story_articles(session, story)
    return articles[0] if articles else None


@dataclass
class StoryCard:
    """Everything a card or page needs, assembled once (§34.2, §34.3)."""

    story: Story
    headline: Rendered
    summary: Rendered | None
    sources: list[Source]
    tags: list[Tag]
    articles: list[Article]
    image_url: str | None

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def published_at(self) -> datetime | None:
        return self.story.last_updated_at or self.story.first_reported_at


def build_card(session: Session, story: Story) -> StoryCard:
    articles = story_articles(session, story)
    image = next((a.original_image_url for a in articles if a.original_image_url), None)
    return StoryCard(
        story=story,
        headline=headline(session, story, articles),
        summary=summary(session, story, articles),
        sources=sources_for(session, story),
        tags=tags_for(session, story),
        articles=articles,
        image_url=image,
    )


def relative_time(value: datetime | None, now: datetime | None = None) -> str:
    """"18 minutes ago" — the form §35.5 uses, and what a reader scans for."""
    if value is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = (now - value).total_seconds()
    if seconds < 0:
        return "just now"
    minutes = seconds / 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        n = int(minutes)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    hours = minutes / 60
    if hours < 24:
        n = int(hours)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    days = int(hours / 24)
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"
