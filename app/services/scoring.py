"""Importance and trending — §23, §25, §35.5.

§25 sets a specific trap and asks us to avoid it: a story that suddenly receives many
articles should rise rapidly, but *a topic that normally receives dozens of articles
must not automatically be treated as breaking news*. So trending is measured against
each story's own tags' baseline rate, not against an absolute article count. A sports
league that always generates twenty articles an hour has to beat twenty to trend.

Every factor is stored separately in `scoring_factors`, because §25 requires that an
administrator be able to see why a story is trending, and a single number cannot be
explained after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Article, ScoringFactor, Source, Story, StoryTag

#: Coverage in the last hours counts as "current" for trend purposes.
BURST_WINDOW = timedelta(hours=3)
#: How far back to measure what "normal" looks like for these tags.
BASELINE_WINDOW = timedelta(days=7)

#: The rate a genuinely quiet topic is treated as having. Without a floor, a topic with
#: no history would divide by zero — and treating "no baseline" as "raw article count"
#: inverted the rule: a busy topic scored HIGHER because its ratio had a real
#: denominator while a quiet one did not. The floor makes both comparable.
MIN_BASELINE_PER_HOUR = 0.5


@dataclass(frozen=True)
class Factor:
    name: str
    value: float
    weight: float
    explanation: str

    @property
    def contribution(self) -> float:
        return self.value * self.weight


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_importance(session: Session, story: Story, now: datetime | None = None) -> list[Factor]:
    """How much this story matters, independent of how fast it is moving (§23)."""
    now = now or datetime.now(timezone.utc)
    articles = list(
        session.execute(select(Article).where(Article.story_id == story.id)).scalars().all()
    )
    source_ids = {a.source_id for a in articles}
    priorities = (
        session.execute(select(Source.priority).where(Source.id.in_(source_ids))).scalars().all()
        if source_ids
        else []
    )

    source_count = len(source_ids)
    avg_priority = (sum(priorities) / len(priorities)) if priorities else 0.0

    factors = [
        Factor(
            "independent_sources",
            min(source_count / 6.0, 1.0),
            45.0,
            f"{source_count} independent source{'s' if source_count != 1 else ''}",
        ),
        Factor(
            "article_volume",
            min(len(articles) / 10.0, 1.0),
            20.0,
            f"{len(articles)} article{'s' if len(articles) != 1 else ''} in the story",
        ),
        Factor(
            "source_priority",
            _clamp(avg_priority / 10.0, 0.0, 1.0),
            20.0,
            f"average source priority {avg_priority:.1f}",
        ),
    ]

    if story.is_verified:
        factors.append(Factor("verified", 1.0, 15.0, "marked verified by an editor"))
    if story.has_unresolved_conflicts:
        factors.append(
            Factor("has_conflicts", 1.0, 10.0, "sources disagree — worth a reader's attention")
        )
    return factors


def _baseline_rate(session: Session, story: Story, now: datetime) -> tuple[float, list[int]]:
    """Articles per hour normally seen on this story's tags (§25).

    This is the number that stops a busy topic from permanently 'trending'.
    """
    tag_ids = list(
        session.execute(select(StoryTag.tag_id).where(StoryTag.story_id == story.id))
        .scalars()
        .all()
    )
    if not tag_ids:
        return 0.0, []

    since = now - BASELINE_WINDOW
    from app.models import ArticleTag  # local import keeps the module graph flat

    total = session.execute(
        select(func.count(func.distinct(Article.id)))
        .select_from(Article)
        .join(ArticleTag, ArticleTag.article_id == Article.id)
        .where(ArticleTag.tag_id.in_(tag_ids), Article.original_published_at >= since)
    ).scalar_one()
    hours = BASELINE_WINDOW.total_seconds() / 3600
    return (float(total) / hours if hours else 0.0), tag_ids


def score_trending(session: Session, story: Story, now: datetime | None = None) -> list[Factor]:
    """How fast this story is developing, relative to its own topic's normal (§25)."""
    now = now or datetime.now(timezone.utc)
    articles = list(
        session.execute(select(Article).where(Article.story_id == story.id)).scalars().all()
    )
    if not articles:
        return []

    recent = [
        a
        for a in articles
        if (a.original_published_at or a.fetched_at or now) >= now - BURST_WINDOW
    ]
    recent_rate = len(recent) / (BURST_WINDOW.total_seconds() / 3600)
    baseline, tag_ids = _baseline_rate(session, story, now)

    # Ratio against the topic's own normal — the §25 requirement in one number. The
    # denominator is always at least MIN_BASELINE_PER_HOUR so that a quiet topic and a
    # busy one are measured on the same scale.
    denominator = max(baseline, MIN_BASELINE_PER_HOUR)
    ratio = recent_rate / denominator
    hours = int(BURST_WINDOW.total_seconds() // 3600)
    count = f"{len(recent)} article{'s' if len(recent) != 1 else ''} in the last {hours}h"
    burst_explanation = (
        f"{count} vs a baseline of {baseline:.1f}/h for these topics"
        if baseline > 0
        else f"{count} (no established baseline for these topics)"
    )

    last = story.last_updated_at or now
    hours_old = max((now - last).total_seconds() / 3600, 0.0)
    recency = 1.0 / (1.0 + hours_old / 6.0)

    source_count = len({a.source_id for a in articles})

    return [
        Factor("coverage_burst", min(ratio / 4.0, 1.0), 40.0, burst_explanation),
        Factor("recency", recency, 25.0, f"last update {hours_old:.1f}h ago"),
        Factor(
            "independent_sources",
            min(source_count / 6.0, 1.0),
            25.0,
            f"{source_count} independent source{'s' if source_count != 1 else ''}",
        ),
        Factor(
            "historical_baseline",
            1.0 - min(baseline / 20.0, 1.0),
            10.0,
            f"topic baseline {baseline:.1f} articles/hour"
            + (" (busy topic — harder to trend)" if baseline > 5 else ""),
        ),
    ]


def _persist(
    session: Session, story: Story, kind: str, factors: list[Factor], now: datetime
) -> float:
    """Store the factors and return the total (§25, §35.5)."""
    session.query(ScoringFactor).filter(
        ScoringFactor.story_id == story.id, ScoringFactor.score_kind == kind
    ).delete(synchronize_session=False)

    total = 0.0
    for factor in factors:
        total += factor.contribution
        session.add(
            ScoringFactor(
                story_id=story.id,
                score_kind=kind,
                factor=factor.name,
                value=round(factor.value, 4),
                weight=factor.weight,
                explanation=factor.explanation,
                computed_at=now,
            )
        )
    return round(_clamp(total), 2)


def rescore(session: Session, story: Story, now: datetime | None = None) -> tuple[float, float]:
    """Recompute both scores for a story and store the reasoning. Idempotent."""
    now = now or datetime.now(timezone.utc)
    importance = _persist(session, story, "importance", score_importance(session, story, now), now)
    trending = _persist(session, story, "trending", score_trending(session, story, now), now)
    story.importance_score = importance
    story.trending_score = trending
    session.flush()
    return importance, trending


def explain(session: Session, story: Story, kind: str = "trending") -> list[str]:
    """The lines behind a score, for the "why am I seeing this?" panel (§35.5)."""
    rows = (
        session.execute(
            select(ScoringFactor)
            .where(ScoringFactor.story_id == story.id, ScoringFactor.score_kind == kind)
            .order_by(ScoringFactor.weight.desc())
        )
        .scalars()
        .all()
    )
    return [row.explanation for row in rows if row.explanation]
