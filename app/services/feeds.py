"""Feed membership — §29, §29.3, §35.5.

A feed instance is a saved query. Evaluating it produces `feed_instance_entries`, which
the public pages read directly — so a page render is a single indexed lookup and never
runs the rule engine (Non-Negotiable #7).

Every entry records *why* it qualified. That string is what the "why am I seeing this?"
panel shows (§35.5), and it is the difference between a feed you can debug and a feed
you have to guess at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Article,
    FeedInstance,
    FeedInstanceEntry,
    FeedInstanceSource,
    FeedInstanceSourceType,
    FeedInstanceTag,
    Source,
    Story,
    StoryTag,
    Tag,
)

logger = logging.getLogger("feeds")


@dataclass
class FeedRules:
    """A feed's composition, resolved once so matching is cheap per story."""

    included_tags: set[int]
    excluded_tags: set[int]
    required_tags: set[int]
    included_sources: set[int]
    excluded_sources: set[int]
    included_source_types: set[int]
    excluded_source_types: set[int]
    min_importance: float | None
    min_trending: float | None

    @property
    def has_positive_rule(self) -> bool:
        """A feed with no inclusion rule at all means 'everything'."""
        return bool(self.included_tags or self.included_sources or self.included_source_types)


def load_rules(session: Session, feed: FeedInstance) -> FeedRules:
    tag_rules = (
        session.execute(
            select(FeedInstanceTag).where(FeedInstanceTag.feed_instance_id == feed.id)
        )
        .scalars()
        .all()
    )
    source_rules = (
        session.execute(
            select(FeedInstanceSource).where(FeedInstanceSource.feed_instance_id == feed.id)
        )
        .scalars()
        .all()
    )
    type_rules = (
        session.execute(
            select(FeedInstanceSourceType).where(
                FeedInstanceSourceType.feed_instance_id == feed.id
            )
        )
        .scalars()
        .all()
    )
    return FeedRules(
        included_tags={r.tag_id for r in tag_rules if r.mode == "include"},
        excluded_tags={r.tag_id for r in tag_rules if r.mode == "exclude"},
        required_tags={r.tag_id for r in tag_rules if r.is_required},
        included_sources={r.source_id for r in source_rules if r.mode == "include"},
        excluded_sources={r.source_id for r in source_rules if r.mode == "exclude"},
        included_source_types={r.source_type_id for r in type_rules if r.mode == "include"},
        excluded_source_types={r.source_type_id for r in type_rules if r.mode == "exclude"},
        min_importance=feed.min_importance_score,
        min_trending=feed.min_trending_score,
    )


@dataclass(frozen=True)
class MatchResult:
    matches: bool
    reason: str


def match_story(
    session: Session, feed: FeedInstance, rules: FeedRules, story: Story
) -> MatchResult:
    """Decide whether a story belongs in a feed, and say why (§29, §35.5).

    Exclusions are evaluated before inclusions: §29's example excludes Opinion from
    Power Grid News, and a story tagged both must be excluded. An exclusion is a
    statement about what the publication is *not*, and it wins.
    """
    if story.is_hidden:
        return MatchResult(False, "story is hidden")

    story_tag_ids = set(
        session.execute(select(StoryTag.tag_id).where(StoryTag.story_id == story.id))
        .scalars()
        .all()
    )
    article_rows = session.execute(
        select(Article.source_id).where(Article.story_id == story.id)
    ).all()
    story_source_ids = {row[0] for row in article_rows}
    source_type_ids = set(
        session.execute(
            select(Source.source_type_id).where(Source.id.in_(story_source_ids or {0}))
        )
        .scalars()
        .all()
    ) - {None}

    # --- exclusions first ---
    if rules.excluded_tags & story_tag_ids:
        names = _tag_names(session, rules.excluded_tags & story_tag_ids)
        return MatchResult(False, f"excluded tag: {', '.join(names)}")
    if rules.excluded_sources & story_source_ids:
        return MatchResult(False, "excluded source")
    if rules.excluded_source_types & source_type_ids:
        return MatchResult(False, "excluded source type")

    # --- required tags ---
    missing = rules.required_tags - story_tag_ids
    if missing:
        names = ", ".join(_tag_names(session, missing))
        return MatchResult(False, f"missing required tag: {names}")

    # --- thresholds ---
    if rules.min_importance is not None and (story.importance_score or 0) < rules.min_importance:
        return MatchResult(
            False,
            f"importance {story.importance_score or 0:.0f} below threshold "
            f"{rules.min_importance:.0f}",
        )
    if rules.min_trending is not None and (story.trending_score or 0) < rules.min_trending:
        return MatchResult(
            False,
            f"trending {story.trending_score or 0:.0f} below threshold {rules.min_trending:.0f}",
        )

    # --- inclusions ---
    reasons: list[str] = []
    matched_tags = rules.included_tags & story_tag_ids
    if matched_tags:
        reasons.append(f"tag: {', '.join(_tag_names(session, matched_tags))}")
    if rules.included_sources & story_source_ids:
        reasons.append("included source")
    if rules.included_source_types & source_type_ids:
        reasons.append("included source type")

    if rules.has_positive_rule and not reasons:
        return MatchResult(False, "matched no inclusion rule")
    if not rules.has_positive_rule:
        reasons.append("feed has no inclusion rules — all stories qualify")

    if story.importance_score is not None:
        reasons.append(f"importance {story.importance_score:.0f}")
    if story.trending_score is not None:
        reasons.append(f"trending {story.trending_score:.0f}")
    reasons.append(f"{story.source_count} source{'s' if story.source_count != 1 else ''}")
    return MatchResult(True, "; ".join(reasons))


def _tag_names(session: Session, tag_ids: set[int]) -> list[str]:
    if not tag_ids:
        return []
    return sorted(
        session.execute(select(Tag.slug).where(Tag.id.in_(tag_ids))).scalars().all()
    )


def _rank(feed: FeedInstance, story: Story) -> float:
    if feed.sort_order == "trending":
        return story.trending_score or 0.0
    if feed.sort_order == "importance":
        return story.importance_score or 0.0
    if feed.sort_order == "most_sources":
        return float(story.source_count or 0)
    published = story.last_updated_at or story.first_reported_at
    return published.timestamp() if published else 0.0


def publish_story(
    session: Session, story: Story, now: datetime | None = None
) -> list[tuple[int, str]]:
    """Place one story into every feed whose rules it satisfies (§11 step 16).

    Idempotent — re-running updates the existing entry rather than duplicating it, so
    the stage is safe to retry (Non-Negotiable #8).
    """
    now = now or datetime.now(timezone.utc)
    feeds = (
        session.execute(select(FeedInstance).where(FeedInstance.is_active.is_(True)))
        .scalars()
        .all()
    )
    placed: list[tuple[int, str]] = []

    for feed in feeds:
        rules = load_rules(session, feed)
        result = match_story(session, feed, rules, story)
        existing = session.get(
            FeedInstanceEntry, {"feed_instance_id": feed.id, "story_id": story.id}
        )

        if not result.matches:
            if existing is not None:
                session.delete(existing)
            continue

        published_at = story.last_updated_at or story.first_reported_at or now
        rank = _rank(feed, story)
        if existing is None:
            session.add(
                FeedInstanceEntry(
                    feed_instance_id=feed.id,
                    story_id=story.id,
                    published_at=published_at,
                    rank_score=rank,
                    match_reason=result.reason[:500],
                )
            )
        else:
            existing.published_at = published_at
            existing.rank_score = rank
            existing.match_reason = result.reason[:500]
        placed.append((feed.id, result.reason))

    session.flush()
    return placed


def refresh_feed_health(session: Session, feed: FeedInstance, now: datetime | None = None) -> None:
    """Update the counters the feed health screen shows (§29.3)."""
    from datetime import timedelta

    now = now or datetime.now(timezone.utc)
    entries = (
        session.execute(
            select(FeedInstanceEntry).where(FeedInstanceEntry.feed_instance_id == feed.id)
        )
        .scalars()
        .all()
    )
    feed.story_count = len(entries)
    feed.stories_last_24h = sum(1 for e in entries if e.published_at >= now - timedelta(hours=24))
    feed.last_built_at = now
    session.flush()


def feed_stories(session: Session, feed: FeedInstance, limit: int | None = None) -> list[Story]:
    """The stories in a feed, in the feed's sort order — one indexed read (§32)."""
    limit = limit or feed.max_articles or 50
    order = (
        FeedInstanceEntry.published_at.desc()
        if feed.sort_order == "newest"
        else FeedInstanceEntry.rank_score.desc()
    )
    return list(
        session.execute(
            select(Story)
            .join(FeedInstanceEntry, FeedInstanceEntry.story_id == Story.id)
            .where(
                FeedInstanceEntry.feed_instance_id == feed.id,
                Story.is_hidden.is_(False),
            )
            .order_by(order)
            .limit(limit)
        )
        .scalars()
        .all()
    )
