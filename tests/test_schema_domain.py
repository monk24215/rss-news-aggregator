"""Domain rules the schema itself enforces.

Constraints are cheaper than code review: a rule expressed as a CHECK cannot be
forgotten by the next feature. These tests pin the ones that encode spec requirements,
so that removing a constraint breaks a test rather than quietly widening what the system
will accept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AIResult,
    AIResultInput,
    Article,
    ArticleTag,
    FeedInstance,
    FeedInstanceEntry,
    FeedInstanceTag,
    ReviewQueueItem,
    ScoringFactor,
    Source,
    SourceType,
    Story,
    StoryArticle,
    StoryClaim,
    StoryClaimValue,
    StoryTimelineEvent,
    Tag,
)

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime.now(timezone.utc)


def _source(db, slug="reuters", **kw) -> Source:
    src = Source(name=slug.title(), slug=slug, feed_url=f"https://{slug}.example/rss", **kw)
    db.add(src)
    db.flush()
    return src


def _article(db, source, guid="g1", url="https://example.com/a", flush=True, **kw) -> Article:
    fields = dict(
        source_id=source.id,
        original_guid=guid,
        original_url=url,
        original_headline="Headline",
        original_published_at=NOW,
    )
    fields.update(kw)
    art = Article(**fields)
    db.add(art)
    if flush:
        db.flush()
    return art


# --- Deduplication (§13) -----------------------------------------------------


def test_same_guid_from_same_source_is_rejected(db):
    src = _source(db, "dedup-guid")
    _article(db, src, guid="abc")
    _article(db, src, guid="abc", url="https://example.com/other", flush=False)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_same_guid_from_different_sources_is_allowed(db):
    """Two publications legitimately reuse ids; dedup is per source (§13)."""
    a = _source(db, "dedup-a")
    b = _source(db, "dedup-b")
    _article(db, a, guid="shared")
    _article(db, b, guid="shared")
    db.flush()  # no error


def test_normalized_url_is_unique_per_source(db):
    """§13's tracking-parameter example: both URLs normalize to one row."""
    src = _source(db, "dedup-url")
    _article(db, src, guid="1", normalized_url="example.com/story?id=123")
    _article(db, src, guid="2", normalized_url="example.com/story?id=123", flush=False)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_article_cannot_be_its_own_duplicate(db):
    src = _source(db, "dedup-self")
    art = _article(db, src)
    art.duplicate_of_id = art.id
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- Tags (§8) ---------------------------------------------------------------


def test_tag_hierarchy(db):
    energy = Tag(name="Energy", slug="energy")
    db.add(energy)
    db.flush()
    grid = Tag(name="Power Grid", slug="power-grid", parent_id=energy.id)
    db.add(grid)
    db.flush()
    db.refresh(energy)
    assert [c.slug for c in energy.children] == ["power-grid"]
    assert grid.parent.slug == "energy"


def test_tag_cannot_be_its_own_parent(db):
    tag = Tag(name="Loop", slug="loop")
    db.add(tag)
    db.flush()
    tag.parent_id = tag.id
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_tag_confidence_must_be_a_probability(db):
    src = _source(db, "tag-conf")
    art = _article(db, src)
    tag = Tag(name="Grid", slug="grid-conf")
    db.add(tag)
    db.flush()
    db.add(ArticleTag(article_id=art.id, tag_id=tag.id, confidence=1.4))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- Sources (§7, §12) -------------------------------------------------------


def test_fetch_interval_cannot_be_impolite(db):
    """§12 asks for politeness; a 5-second poll is not that."""
    db.add(
        Source(
            name="Hammer",
            slug="hammer",
            feed_url="https://x.example/rss",
            fetch_interval_seconds=5,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_source_context_is_from_the_known_vocabulary(db):
    """§7.2 — context is descriptive; "trustworthy" is not one of the options."""
    db.add(
        Source(
            name="Bad", slug="bad-context", feed_url="https://x.example/rss", context="trustworthy"
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_source_type_is_configurable_not_hardcoded(db):
    """§7.1 — adding a type is a row, not a migration."""
    db.add(SourceType(name="Podcast", slug="podcast"))
    db.flush()
    src = _source(db, "typed")
    src.source_type_id = db.query(SourceType).filter_by(slug="podcast").one().id
    db.flush()
    assert src.source_type.name == "Podcast"


# --- Stories (§21, §22, §24) -------------------------------------------------


def test_story_state_is_from_the_lifecycle(db):
    db.add(Story(state="exploding"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_story_gathers_articles_from_many_sources(db):
    """The core distinction in §3: four reports, one story."""
    story = Story(state="developing")
    db.add(story)
    db.flush()
    for i, name in enumerate(["reuters", "ap", "local", "trade"]):
        src = _source(db, f"multi-{name}")
        art = _article(db, src, guid=f"g{i}", url=f"https://{name}.example/x", story_id=story.id)
        db.add(
            StoryArticle(
                story_id=story.id, article_id=art.id, is_first_report=(i == 0), confidence=0.92
            )
        )
    db.flush()
    db.refresh(story)
    assert len(story.articles) == 4
    assert sum(m.is_first_report for m in story.memberships) == 1


def test_timeline_entry_must_cite_reporting_or_be_marked_manual(db):
    """§22: every timeline entry identifies the source article behind it."""
    story = Story()
    db.add(story)
    db.flush()
    db.add(
        StoryTimelineEvent(story_id=story.id, occurred_at=NOW, summary="Something happened")
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_timeline_entry_with_a_source_article_is_accepted(db):
    src = _source(db, "timeline")
    story = Story()
    db.add(story)
    db.flush()
    art = _article(db, src, story_id=story.id)
    db.add(
        StoryTimelineEvent(
            story_id=story.id,
            occurred_at=NOW,
            summary="First report",
            source_article_id=art.id,
        )
    )
    db.flush()  # no error


def test_conflicting_claims_are_recorded_not_reconciled(db):
    """§24 — both numbers survive, attributed, until something resolves them."""
    story = Story(has_unresolved_conflicts=True)
    db.add(story)
    db.flush()
    claim = StoryClaim(story_id=story.id, subject="customers affected")
    db.add(claim)
    db.flush()

    a = _source(db, "claim-a")
    b = _source(db, "claim-b")
    db.add(StoryClaimValue(claim_id=claim.id, source_id=a.id, value="50,000"))
    db.add(StoryClaimValue(claim_id=claim.id, source_id=b.id, value="65,000"))
    db.flush()
    db.refresh(claim)

    assert {v.value for v in claim.values} == {"50,000", "65,000"}
    assert claim.is_conflicting is True
    assert claim.resolved_at is None


def test_a_claim_can_later_be_resolved(db):
    story = Story()
    db.add(story)
    db.flush()
    claim = StoryClaim(story_id=story.id, subject="cause")
    db.add(claim)
    db.flush()
    value = StoryClaimValue(claim_id=claim.id, value="equipment failure")
    db.add(value)
    db.flush()

    claim.resolved_value_id = value.id
    claim.resolved_at = NOW
    claim.is_conflicting = False
    claim.resolution_note = "Utility confirmed in official statement"
    db.flush()
    assert claim.resolved_value_id == value.id


# --- Scoring (§23, §25, §35.5) ----------------------------------------------


def test_scoring_factors_are_stored_individually(db):
    """§25: the parts, not just the total, so 'why is this trending' is answerable."""
    story = Story(trending_score=91.0)
    db.add(story)
    db.flush()
    for factor, value, explanation in [
        ("independent_sources", 8, "8 independent sources"),
        ("articles_per_hour", 14, "14 articles in 45 minutes"),
        ("historical_baseline", 2, "Historical hourly coverage: 2 articles"),
    ]:
        db.add(
            ScoringFactor(
                story_id=story.id,
                score_kind="trending",
                factor=factor,
                value=value,
                explanation=explanation,
                computed_at=NOW,
            )
        )
    db.flush()
    rows = db.query(ScoringFactor).filter_by(story_id=story.id).all()
    assert len(rows) == 3
    assert any("independent sources" in r.explanation for r in rows)


def test_a_scoring_factor_must_describe_something(db):
    db.add(ScoringFactor(score_kind="trending", factor="orphan", computed_at=NOW))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_trending_score_stays_in_range(db):
    db.add(Story(trending_score=140.0))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- Feeds (§29) -------------------------------------------------------------


def _feed(db, slug="power-grid") -> FeedInstance:
    feed = FeedInstance(name="Power Grid News", slug=slug)
    db.add(feed)
    db.flush()
    return feed


def test_feed_include_and_exclude_rules(db):
    feed = _feed(db)
    included = Tag(name="Power Grid", slug="pg-inc")
    excluded = Tag(name="Opinion", slug="opinion-exc")
    db.add_all([included, excluded])
    db.flush()
    db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=included.id, mode="include"))
    db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=excluded.id, mode="exclude"))
    db.flush()
    db.refresh(feed)
    modes = {rule.mode for rule in feed.tags}
    assert modes == {"include", "exclude"}


def test_an_excluded_tag_cannot_also_be_required(db):
    """A rule that contradicts itself should not be storable."""
    feed = _feed(db, "contradiction")
    tag = Tag(name="Reviews", slug="reviews-x")
    db.add(tag)
    db.flush()
    db.add(
        FeedInstanceTag(
            feed_instance_id=feed.id, tag_id=tag.id, mode="exclude", is_required=True
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_feed_sort_order_is_from_the_known_set(db):
    db.add(FeedInstance(name="Bad", slug="bad-sort", sort_order="alphabetical"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_feed_membership_records_why_a_story_qualified(db):
    """§35.5 — the 'why am I seeing this?' panel reads this row."""
    feed = _feed(db, "why-feed")
    story = Story(trending_score=91.0, importance_score=82.0)
    db.add(story)
    db.flush()
    db.add(
        FeedInstanceEntry(
            feed_instance_id=feed.id,
            story_id=story.id,
            published_at=NOW,
            rank_score=91.0,
            match_reason="tag:power-grid; importance:82; trending:91",
        )
    )
    db.flush()
    entry = db.query(FeedInstanceEntry).filter_by(feed_instance_id=feed.id).one()
    assert "power-grid" in entry.match_reason


# --- Synthesis provenance (§15, §20, §34.5) ----------------------------------


def test_a_synthesis_records_every_article_it_was_built_from(db):
    """§15: store the source articles used to generate every synthesized story.

    This is what makes "Generated from 6 source reports" (§34.5) checkable rather than
    decorative.
    """
    story = Story(state="active")
    db.add(story)
    db.flush()

    articles = []
    for i in range(6):
        src = _source(db, f"syn-{i}")
        articles.append(_article(db, src, guid=f"s{i}", url=f"https://s{i}.example/x",
                                 story_id=story.id))

    synthesis = AIResult(
        target_type="story",
        story_id=story.id,
        operation="synthesize_story",
        content="Six outlets report a regional outage.",
        provider="anthropic",
        model="claude-x",
        generated_at=NOW,
        confidence=0.92,
    )
    db.add(synthesis)
    db.flush()
    for art in articles:
        db.add(AIResultInput(ai_result_id=synthesis.id, article_id=art.id))
    db.flush()
    db.refresh(synthesis)

    assert len(synthesis.inputs) == 6


# --- Review queue (§27.2) ----------------------------------------------------


def test_low_confidence_work_can_be_queued_for_a_human(db):
    story = Story()
    db.add(story)
    db.flush()
    db.add(
        ReviewQueueItem(
            reason="story_merge",
            story_id=story.id,
            confidence=0.41,
            detail={"candidate_story_id": 99},
        )
    )
    db.flush()
    item = db.query(ReviewQueueItem).filter_by(story_id=story.id).one()
    assert item.state == "pending_review"


def test_a_review_item_must_reference_something(db):
    db.add(ReviewQueueItem(reason="orphan"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- End to end --------------------------------------------------------------


def test_the_power_outage_walkthrough(db):
    """Appendix A end to end: four reports become one story in one feed.

    Not a unit test — a shape test. If the schema can hold the spec's own worked
    example, the model is at least the right shape for the product.
    """
    grid = Tag(name="Power Grid", slug="pg-e2e")
    db.add(grid)
    db.flush()

    story = Story(state="developing", first_reported_at=NOW, source_count=4, article_count=4)
    db.add(story)
    db.flush()

    for i, name in enumerate(["reuters", "ap", "local", "trade"]):
        src = _source(db, f"e2e-{name}")
        art = _article(
            db,
            src,
            guid=f"e2e{i}",
            url=f"https://{name}.example/outage",
            story_id=story.id,
            original_published_at=NOW + timedelta(minutes=i * 15),
        )
        db.add(ArticleTag(article_id=art.id, tag_id=grid.id, confidence=0.98))
        db.add(StoryArticle(story_id=story.id, article_id=art.id, is_first_report=(i == 0)))
        db.add(
            StoryTimelineEvent(
                story_id=story.id,
                occurred_at=NOW + timedelta(minutes=i * 15),
                summary=f"{name} reports",
                source_article_id=art.id,
            )
        )

    claim = StoryClaim(story_id=story.id, subject="customers affected")
    db.add(claim)
    db.flush()
    db.add(StoryClaimValue(claim_id=claim.id, value="50,000"))
    db.add(StoryClaimValue(claim_id=claim.id, value="65,000"))

    feed = _feed(db, "e2e-power-grid")
    db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=grid.id, mode="include"))
    db.add(
        FeedInstanceEntry(
            feed_instance_id=feed.id,
            story_id=story.id,
            published_at=NOW,
            rank_score=91.0,
            match_reason="tag:pg-e2e",
        )
    )
    db.flush()
    db.refresh(story)

    assert len(story.articles) == 4
    assert len(story.timeline) == 4
    assert len(story.claims[0].values) == 2
    # Every article still links back to its publisher (Non-Negotiable #2).
    assert all(a.original_url.startswith("https://") for a in story.articles)
