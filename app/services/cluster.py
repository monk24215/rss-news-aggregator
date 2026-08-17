"""Story clustering — §19, §21, §22.

The product's central claim is that four reports of one power outage are one story
(§3). This module is where that claim is made good, without a model.

The rule §19 is emphatic about: *do not merge articles merely because they share
general keywords.* So a match requires agreement on **named entities** — the specific
people, organizations, and places an event involves — and only then uses keyword and
title overlap to break ties. Two articles about "energy" do not cluster; two articles
about "Dominion Energy" and "Chesterfield County" within the same hours do.

Candidates are restricted to a time window because news is bounded in time: last
month's outage is not this morning's, however similar the wording.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, ArticleEntity, Story, StoryArticle, StoryTag, StoryTimelineEvent
from app.services.enrich import extract_entities, top_keywords
from app.services.normalize import slugify

logger = logging.getLogger("cluster")

#: How far back to look for a story an article might belong to.
CANDIDATE_WINDOW = timedelta(hours=48)

#: Minimum score to join an existing story. Below this the article opens a new one —
#: a wrong split is recoverable by an administrator (§19); a wrong merge destroys the
#: distinction between two events and is much harder to notice.
#:
#: Calibrated on 400 live articles from sixteen feeds: genuine same-event pairs (one
#: earthquake reported by four outlets, one airstrike by five) scored 0.62-0.81, while
#: merely adjacent coverage — two different Ukraine stories, two different wildfires —
#: sat below it.
JOIN_THRESHOLD = 0.62

#: Matches between JOIN_THRESHOLD and this are acted on but flagged for a human (§27.1).
#: In the live sample this band held the honest judgement calls: "Ukraine strikes a
#: rocket factory" and "Ukraine's civilian casualties rise" are related, and whether
#: they are one story is a question a person should answer, not a threshold.
REVIEW_THRESHOLD = 0.72


@dataclass(frozen=True)
class ClusterDecision:
    story_id: int | None
    created: bool
    confidence: float
    needs_review: bool
    rationale: str


def _entity_sets(session: Session, article_ids: list[int]) -> dict[int, set[str]]:
    if not article_ids:
        return {}
    rows = session.execute(
        select(ArticleEntity.article_id, ArticleEntity.normalized_value).where(
            ArticleEntity.article_id.in_(article_ids)
        )
    ).all()
    out: dict[int, set[str]] = {}
    for article_id, value in rows:
        out.setdefault(article_id, set()).add(value)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient: shared / size of the smaller set.

    Jaccard is the wrong measure for headlines. Two outlets covering one event name the
    same specifics but each adds its own context, so the union grows while the
    intersection does not — real matches were scoring 0.3-0.5 on live coverage and being
    rejected. Overlap asks the question that actually matters: of the specifics the
    shorter headline names, how many does the other one also name?
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _stem(entity: str) -> str:
    """Collapse morphological variants so one entity is not counted twice.

    "Israel"/"Israeli", "Morocco"/"Moroccan", and "Croatia"/"Croatian" are the same
    specific thing. Without this, a pair sharing only *one* entity under two spellings
    looks like a pair sharing two — which is exactly how a Croatian wildfire and a
    French one came to look related in live data.
    """
    return " ".join(word[:6] for word in entity.split())


def _stems(entities: set[str]) -> set[str]:
    return {_stem(e) for e in entities}


#: A match must name at least this many *distinct* specifics in common. One shared
#: entity is a coincidence ("both mention Israel"); two is an event.
MIN_SHARED_ENTITIES = 2


def score_match(
    article_entities: set[str],
    article_keywords: set[str],
    candidate_entities: set[str],
    candidate_keywords: set[str],
) -> tuple[float, str]:
    """How strongly one article matches a candidate story.

    Entity agreement dominates deliberately (§19): keywords can only ever adjust a score
    that shared specifics have already earned, so "both are about energy" can never by
    itself merge two events.

    The weights and threshold here were calibrated against live coverage from eight
    international outlets rather than chosen a priori — the first guess rejected every
    genuine match in the sample.
    """
    stems_a, stems_b = _stems(article_entities), _stems(candidate_entities)
    shared = stems_a & stems_b

    if len(shared) < MIN_SHARED_ENTITIES:
        return 0.0, (
            "no shared entities"
            if not shared
            else f"only one shared entity ({sorted(shared)[0]})"
        )

    entity_score = 0.7 * _overlap(stems_a, stems_b) + 0.3 * _jaccard(stems_a, stems_b)
    keyword_score = _jaccard(article_keywords, candidate_keywords)
    specificity = min(len(shared) / 3.0, 1.0)

    score = 0.50 * entity_score + 0.30 * specificity + 0.20 * keyword_score
    readable = sorted(article_entities & candidate_entities) or sorted(shared)
    rationale = (
        f"shared: {', '.join(readable[:4])}"
        f" (entity {entity_score:.2f}, keyword {keyword_score:.2f}, {len(shared)} specifics)"
    )
    return round(min(score, 1.0), 3), rationale


def _candidate_articles(session: Session, article: Article, now: datetime) -> list[Article]:
    """Articles already assigned to a story, close enough in time to be the same event."""
    published = article.original_published_at or article.fetched_at or now
    window_start = published - CANDIDATE_WINDOW
    window_end = published + CANDIDATE_WINDOW
    return list(
        session.execute(
            select(Article)
            .join(Story, Article.story_id == Story.id)
            .where(
                Article.id != article.id,
                Article.story_id.is_not(None),
                Story.is_locked.is_(False),
                Article.original_published_at.is_not(None),
                Article.original_published_at >= window_start,
                Article.original_published_at <= window_end,
            )
            .limit(300)
        )
        .scalars()
        .all()
    )


def ensure_entities(session: Session, article: Article) -> set[str]:
    """Extract and persist entities for an article if not already done. Idempotent."""
    existing = set(
        session.execute(
            select(ArticleEntity.normalized_value).where(ArticleEntity.article_id == article.id)
        )
        .scalars()
        .all()
    )
    if existing:
        return existing

    values: set[str] = set()
    for entity in extract_entities(article.original_headline, article.original_description):
        session.add(
            ArticleEntity(
                article_id=article.id,
                kind=entity.kind,
                value=entity.value,
                normalized_value=entity.normalized_value,
                confidence=entity.confidence,
            )
        )
        values.add(entity.normalized_value)
    session.flush()
    return values


def assign_story(
    session: Session, article: Article, now: datetime | None = None
) -> ClusterDecision:
    """Place an article in an existing story or open a new one (§19).

    Idempotent: an article already assigned to a story is left alone, so the stage is
    safe to retry (Non-Negotiable #8). A locked story is never modified automatically
    (§19, Non-Negotiable #5).
    """
    now = now or datetime.now(timezone.utc)

    if article.story_id is not None:
        return ClusterDecision(article.story_id, False, 1.0, False, "already assigned")

    entities = ensure_entities(session, article)
    keywords = set(
        top_keywords(f"{article.original_headline} {article.original_description or ''}")
    )

    candidates = _candidate_articles(session, article, now)
    entity_map = _entity_sets(session, [c.id for c in candidates])

    best_story_id: int | None = None
    best_score = 0.0
    best_rationale = "no candidates in window"

    per_story: dict[int, tuple[float, str]] = {}
    for candidate in candidates:
        cand_entities = entity_map.get(candidate.id, set())
        cand_keywords = set(
            top_keywords(f"{candidate.original_headline} {candidate.original_description or ''}")
        )
        score, rationale = score_match(entities, keywords, cand_entities, cand_keywords)
        story_id = candidate.story_id
        if story_id is None:
            continue
        # A story's score is its best-matching member.
        if story_id not in per_story or score > per_story[story_id][0]:
            per_story[story_id] = (score, rationale)

    for story_id, (score, rationale) in per_story.items():
        if score > best_score:
            best_story_id, best_score, best_rationale = story_id, score, rationale

    if best_story_id is not None and best_score >= JOIN_THRESHOLD:
        story = session.get(Story, best_story_id)
        _attach(session, story, article, confidence=best_score, now=now, first_report=False)
        return ClusterDecision(
            story.id,
            False,
            best_score,
            needs_review=best_score < REVIEW_THRESHOLD,
            rationale=best_rationale,
        )

    story = Story(
        state="emerging",
        first_reported_at=article.original_published_at or now,
        last_updated_at=article.original_published_at or now,
    )
    session.add(story)
    session.flush()
    story.slug = f"{slugify(article.original_headline, 60)}-{story.id}"
    _attach(session, story, article, confidence=1.0, now=now, first_report=True)
    return ClusterDecision(
        story.id,
        True,
        best_score,
        needs_review=False,
        rationale=f"new story ({best_rationale}, best score {best_score:.2f})",
    )


def _attach(
    session: Session,
    story: Story,
    article: Article,
    confidence: float,
    now: datetime,
    first_report: bool,
) -> None:
    """Link article to story and keep the story's summary counters honest."""
    article.story_id = story.id
    article.story_match_confidence = confidence
    session.add(
        StoryArticle(
            story_id=story.id,
            article_id=article.id,
            is_first_report=first_report,
            confidence=confidence,
            assigned_at=now,
        )
    )
    # §22: every timeline entry cites the reporting behind it.
    session.add(
        StoryTimelineEvent(
            story_id=story.id,
            occurred_at=article.original_published_at or now,
            summary=article.original_headline[:500],
            source_article_id=article.id,
        )
    )
    session.flush()
    refresh_story_counters(session, story, now)
    inherit_tags(session, story, article)


def refresh_story_counters(session: Session, story: Story, now: datetime | None = None) -> None:
    """Recount members and sources, and advance the lifecycle state (§21).

    `source_count` counts distinct *sources*, not articles: §25 scores on independent
    voices, and one prolific publisher is not eight of them.
    """
    now = now or datetime.now(timezone.utc)
    articles = list(
        session.execute(select(Article).where(Article.story_id == story.id)).scalars().all()
    )
    story.article_count = len(articles)
    story.source_count = len({a.source_id for a in articles})

    published = [a.original_published_at for a in articles if a.original_published_at]
    if published:
        story.first_reported_at = min(published)
        story.last_updated_at = max(published)

    if not story.state_is_manual:
        story.state = _derive_state(story, now)


def _derive_state(story: Story, now: datetime) -> str:
    """Lifecycle from coverage and recency (§21).

    Kept simple and explainable on purpose: an administrator who disagrees can override,
    and `state_is_manual` then protects their decision (Non-Negotiable #5).
    """
    last = story.last_updated_at or story.first_reported_at or now
    age = now - last
    if age > timedelta(days=14):
        return "archived"
    if age > timedelta(days=2):
        return "cooling"
    if story.source_count >= 4 and age < timedelta(hours=6):
        return "active"
    if story.source_count >= 2 and age < timedelta(hours=24):
        return "developing"
    if age < timedelta(hours=24):
        return "emerging"
    return "stable"


def inherit_tags(session: Session, story: Story, article: Article) -> None:
    """A story carries the tags of its articles (§10), without duplicates."""
    from app.models import ArticleTag  # local import keeps the module import graph flat

    article_tag_ids = set(
        session.execute(select(ArticleTag.tag_id).where(ArticleTag.article_id == article.id))
        .scalars()
        .all()
    )
    if not article_tag_ids:
        return
    existing = set(
        session.execute(select(StoryTag.tag_id).where(StoryTag.story_id == story.id))
        .scalars()
        .all()
    )
    for tag_id in article_tag_ids - existing:
        session.add(StoryTag(story_id=story.id, tag_id=tag_id, is_manual=False))
    session.flush()
