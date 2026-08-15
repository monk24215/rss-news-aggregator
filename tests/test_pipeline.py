"""Tagging, clustering, scoring, and feed placement — §8, §19, §25, §29.

The test that matters most here is `test_four_outlets_one_story`: it is the product's
central claim (§3) executed end to end. If that ever fails, the thing being built is an
RSS reader, not a news platform.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    Article,
    ArticleTag,
    FeedInstance,
    FeedInstanceEntry,
    FeedInstanceTag,
    ScoringFactor,
    Source,
    Story,
    StoryTag,
    StoryTimelineEvent,
    Tag,
)
from app.services.cluster import (
    JOIN_THRESHOLD,
    assign_story,
    ensure_entities,
    refresh_story_counters,
    score_match,
)
from app.services.enrich import apply_tags, extract_entities, suggest_tags, top_keywords
from app.services.feeds import feed_stories, load_rules, match_story, publish_story
from app.services.scoring import explain, rescore

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _source(db, slug, priority=5, **kw) -> Source:
    src = Source(
        name=slug.replace("-", " ").title(),
        slug=slug,
        feed_url=f"https://{slug}.example/rss",
        priority=priority,
        **kw,
    )
    db.add(src)
    db.flush()
    return src


def _article(
    db, source, headline, description=None, minutes=0, guid=None, published_at=None
) -> Article:
    # `original_published_at` is set at insert, never after: the database trigger
    # rejects any later change (Non-Negotiable #1), and the tests must respect the same
    # rule the application does.
    art = Article(
        source_id=source.id,
        original_guid=guid or f"{source.slug}-{headline[:20]}-{minutes}",
        original_url=f"https://{source.slug}.example/{abs(hash(headline)) % 10**8}",
        original_headline=headline,
        original_description=description,
        original_published_at=published_at or (NOW + timedelta(minutes=minutes)),
        fetched_at=NOW,
    )
    db.add(art)
    db.flush()
    return art


def _tag(db, name, slug=None) -> Tag:
    tag = Tag(name=name, slug=slug or name.lower().replace(" ", "-"))
    db.add(tag)
    db.flush()
    return tag


# --- Entity extraction -------------------------------------------------------


class TestEntities:
    def test_finds_proper_nouns(self):
        found = {e.normalized_value for e in extract_entities(
            "Dominion Energy restores power to Chesterfield County"
        )}
        assert "dominion energy" in found
        assert "chesterfield county" in found

    def test_ignores_sentence_case_noise(self):
        found = {e.normalized_value for e in extract_entities("The storm hit the coast")}
        assert "the" not in found and "the storm" not in found

    def test_organization_and_location_are_distinguished(self):
        entities = {e.normalized_value: e.kind for e in extract_entities(
            "Dominion Energy Corporation works with Chesterfield County"
        )}
        assert entities.get("dominion energy corporation") == "organization"
        assert entities.get("chesterfield county") == "location"

    def test_headline_beats_summary_for_confidence(self):
        in_headline = extract_entities("Dominion Energy fails", None)[0]
        in_summary = extract_entities("Something happened", "Dominion Energy fails")[0]
        assert in_headline.confidence > in_summary.confidence

    def test_keywords_skip_stopwords(self):
        assert "the" not in top_keywords("The power went out and the power came back")
        assert "power" in top_keywords("The power went out and the power came back")


# --- Tagging -----------------------------------------------------------------


class TestTagging:
    def test_headline_match_scores_higher_than_body(self, db):
        _tag(db, "Power Grid")
        src = _source(db, "tagtest")
        in_title = _article(db, src, "Power Grid failure reported")
        in_title.normalized_title = "power grid failure reported"
        in_body = _article(db, src, "Outage reported", "A power grid failure", minutes=1)
        in_body.normalized_title = "outage reported"
        db.flush()

        assert suggest_tags(db, in_title)[0][1] > suggest_tags(db, in_body)[0][1]

    def test_apply_tags_is_idempotent(self, db):
        _tag(db, "Energy")
        src = _source(db, "tagidem")
        art = _article(db, src, "Energy prices climb")
        art.normalized_title = "energy prices climb"
        db.flush()

        assert apply_tags(db, art) == 1
        assert apply_tags(db, art) == 0
        assert db.query(ArticleTag).filter_by(article_id=art.id).count() == 1

    def test_inactive_tags_are_not_suggested(self, db):
        tag = _tag(db, "Retired")
        tag.is_active = False
        src = _source(db, "tagoff")
        art = _article(db, src, "Retired topic returns")
        art.normalized_title = "retired topic returns"
        db.flush()
        assert suggest_tags(db, art) == []


# --- Clustering --------------------------------------------------------------


class TestClustering:
    def test_shared_topic_alone_does_not_merge(self):
        """§19: do not merge articles merely because they share general keywords."""
        score, reason = score_match(
            {"solar panels"}, {"energy", "prices"},
            {"wind turbines"}, {"energy", "prices"},
        )
        assert score == 0.0
        assert "no shared entities" in reason

    def test_shared_entities_produce_a_match(self):
        score, _ = score_match(
            {"dominion energy", "chesterfield county"}, {"outage", "power"},
            {"dominion energy", "chesterfield county"}, {"outage", "restore"},
        )
        assert score >= JOIN_THRESHOLD

    def test_first_article_opens_a_story(self, db):
        src = _source(db, "cluster-first")
        art = _article(db, src, "Dominion Energy reports outage in Chesterfield County")
        decision = assign_story(db, art, now=NOW)
        assert decision.created is True
        assert art.story_id == decision.story_id

    def test_four_outlets_one_story(self, db):
        """§3's example, executed: four reports of one event become one story."""
        headline = "Dominion Energy outage hits Chesterfield County"
        story_ids = set()
        for i, name in enumerate(["reuters", "ap", "local-paper", "trade-press"]):
            src = _source(db, f"multi-{name}")
            art = _article(
                db,
                src,
                f"{headline}" if i == 0 else f"{headline}, utility says",
                "Crews are working to restore service in Chesterfield County.",
                minutes=i * 20,
            )
            decision = assign_story(db, art, now=NOW)
            story_ids.add(decision.story_id)

        assert len(story_ids) == 1, "four reports of one event should be one story"
        story = db.get(Story, story_ids.pop())
        refresh_story_counters(db, story, now=NOW + timedelta(hours=1))
        assert story.article_count == 4
        assert story.source_count == 4, "source count must count publishers, not articles"
        assert story.state in {"active", "developing"}

    def test_unrelated_events_stay_apart(self, db):
        a_src = _source(db, "unrelated-a")
        b_src = _source(db, "unrelated-b")
        a = _article(db, a_src, "Dominion Energy outage hits Chesterfield County")
        b = _article(db, b_src, "Pacific Gas wildfire warning issued in Sonoma County")
        first = assign_story(db, a, now=NOW)
        second = assign_story(db, b, now=NOW)
        assert first.story_id != second.story_id

    def test_time_window_separates_recurring_events(self, db):
        """Last month's outage is not this morning's, however similar the words."""
        src_a = _source(db, "window-a")
        src_b = _source(db, "window-b")
        old = _article(
            db,
            src_a,
            "Dominion Energy outage hits Chesterfield County",
            published_at=NOW - timedelta(days=30),
        )
        assign_story(db, old, now=NOW - timedelta(days=30))

        new = _article(db, src_b, "Dominion Energy outage hits Chesterfield County again")
        decision = assign_story(db, new, now=NOW)
        assert decision.created is True

    def test_assignment_is_idempotent(self, db):
        src = _source(db, "cluster-idem")
        art = _article(db, src, "Dominion Energy outage hits Chesterfield County")
        first = assign_story(db, art, now=NOW)
        second = assign_story(db, art, now=NOW)
        assert second.story_id == first.story_id
        assert second.created is False

    def test_locked_story_is_never_joined_automatically(self, db):
        """Non-Negotiable #5 — an editor's decision outranks the clusterer."""
        src_a = _source(db, "locked-a")
        first = _article(db, src_a, "Dominion Energy outage hits Chesterfield County")
        decision = assign_story(db, first, now=NOW)
        story = db.get(Story, decision.story_id)
        story.is_locked = True
        db.flush()

        src_b = _source(db, "locked-b")
        second = _article(
            db, src_b, "Dominion Energy outage hits Chesterfield County, officials say", minutes=30
        )
        new_decision = assign_story(db, second, now=NOW)
        assert new_decision.story_id != story.id

    def test_every_membership_writes_a_sourced_timeline_entry(self, db):
        """§22 — a timeline entry always cites the reporting behind it."""
        src = _source(db, "timeline-src")
        art = _article(db, src, "Dominion Energy outage hits Chesterfield County")
        decision = assign_story(db, art, now=NOW)
        events = db.query(StoryTimelineEvent).filter_by(story_id=decision.story_id).all()
        assert len(events) == 1
        assert events[0].source_article_id == art.id

    def test_entities_are_stored_once(self, db):
        src = _source(db, "entities-once")
        art = _article(db, src, "Dominion Energy outage hits Chesterfield County")
        first = ensure_entities(db, art)
        second = ensure_entities(db, art)
        assert first == second


# --- Scoring -----------------------------------------------------------------


class TestScoring:
    def _story_with_sources(self, db, prefix, count, minutes_apart=10, event=None) -> Story:
        """Build one story covered by `count` distinct sources.

        `event` names the entities, because two stories built from the *same* headline
        would legitimately cluster into one — which is the clusterer working, but makes
        for a useless comparison.
        """
        event = event or "Dominion Energy outage hits Chesterfield County"
        story = None
        for i in range(count):
            src = _source(db, f"{prefix}-{i}")
            art = _article(db, src, event, minutes=i * minutes_apart)
            decision = assign_story(db, art, now=NOW)
            story = db.get(Story, decision.story_id)
        return story

    def test_more_independent_sources_scores_higher(self, db):
        small = self._story_with_sources(db, "score-small", 1)
        small_importance, _ = rescore(db, small, now=NOW + timedelta(minutes=30))
        big = self._story_with_sources(db, "score-big", 5)
        big_importance, _ = rescore(db, big, now=NOW + timedelta(minutes=30))
        assert big_importance > small_importance

    def test_factors_are_stored_individually(self, db):
        """§25 — the parts, so 'why is this trending' has an answer."""
        story = self._story_with_sources(db, "score-factors", 3)
        rescore(db, story, now=NOW + timedelta(minutes=30))
        rows = db.query(ScoringFactor).filter_by(story_id=story.id).all()
        kinds = {r.score_kind for r in rows}
        assert kinds == {"importance", "trending"}
        assert all(r.explanation for r in rows)

    def test_explanations_read_like_the_spec_example(self, db):
        story = self._story_with_sources(db, "score-explain", 4)
        rescore(db, story, now=NOW + timedelta(minutes=30))
        lines = " ".join(explain(db, story, "trending"))
        assert "independent source" in lines
        assert "baseline" in lines

    def test_rescoring_replaces_rather_than_accumulates(self, db):
        story = self._story_with_sources(db, "score-replace", 2)
        rescore(db, story, now=NOW + timedelta(minutes=30))
        first = db.query(ScoringFactor).filter_by(story_id=story.id).count()
        rescore(db, story, now=NOW + timedelta(minutes=45))
        assert db.query(ScoringFactor).filter_by(story_id=story.id).count() == first

    def test_a_busy_topic_is_harder_to_trend(self, db):
        """§25's trap: a topic that always gets dozens of articles is not breaking news.

        Two stories with identical burst; one on a topic with a week of heavy background
        coverage. The busy one must not score higher.
        """
        busy_tag = _tag(db, "Busy Topic", "busy-topic")
        quiet_tag = _tag(db, "Quiet Topic", "quiet-topic")

        # A week of heavy background coverage on the busy tag — enough that the topic
        # genuinely runs hot all the time, which is the case §25 warns about.
        background_source = _source(db, "background")
        for i in range(200):
            art = _article(
                db,
                background_source,
                f"Routine item {i}",
                guid=f"bg-{i}",
                published_at=NOW - timedelta(minutes=(i + 1) * 50),
            )
            db.add(ArticleTag(article_id=art.id, tag_id=busy_tag.id, confidence=0.9))
        db.flush()

        # Two genuinely different events, so they cannot cluster into each other.
        busy = self._story_with_sources(
            db, "busy-story", 3, event="Riverton Utilities outage hits Marlow County"
        )
        db.add(StoryTag(story_id=busy.id, tag_id=busy_tag.id))
        quiet = self._story_with_sources(
            db, "quiet-story", 3, event="Halden Water Authority outage hits Presley Township"
        )
        db.add(StoryTag(story_id=quiet.id, tag_id=quiet_tag.id))
        db.flush()
        assert busy.id != quiet.id, "test setup: these must be two separate stories"

        _, busy_trending = rescore(db, busy, now=NOW + timedelta(minutes=30))
        _, quiet_trending = rescore(db, quiet, now=NOW + timedelta(minutes=30))
        assert quiet_trending > busy_trending


# --- Feeds -------------------------------------------------------------------


class TestFeeds:
    def _feed(self, db, slug="power-grid", **kw) -> FeedInstance:
        feed = FeedInstance(name=slug.replace("-", " ").title(), slug=slug, **kw)
        db.add(feed)
        db.flush()
        return feed

    def _story_with_tag(self, db, prefix, tag) -> Story:
        src = _source(db, f"{prefix}-src")
        art = _article(db, src, "Dominion Energy outage hits Chesterfield County")
        decision = assign_story(db, art, now=NOW)
        story = db.get(Story, decision.story_id)
        db.add(StoryTag(story_id=story.id, tag_id=tag.id))
        db.flush()
        rescore(db, story, now=NOW + timedelta(minutes=10))
        return story

    def test_included_tag_places_the_story(self, db):
        tag = _tag(db, "Power Grid", "pg-feed")
        feed = self._feed(db, "pg-news")
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))
        db.flush()
        story = self._story_with_tag(db, "pg", tag)

        placed = publish_story(db, story, now=NOW)
        assert feed.id in [feed_id for feed_id, _ in placed]
        entry = db.query(FeedInstanceEntry).filter_by(feed_instance_id=feed.id).one()
        assert "pg-feed" in entry.match_reason

    def test_exclusion_beats_inclusion(self, db):
        """§29's example excludes Opinion from Power Grid News."""
        included = _tag(db, "Power Grid", "pg-x")
        excluded = _tag(db, "Opinion", "opinion-x")
        feed = self._feed(db, "pg-strict")
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=included.id, mode="include"))
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=excluded.id, mode="exclude"))
        db.flush()

        story = self._story_with_tag(db, "pgx", included)
        db.add(StoryTag(story_id=story.id, tag_id=excluded.id))
        db.flush()

        rules = load_rules(db, feed)
        result = match_story(db, feed, rules, story)
        assert result.matches is False
        assert "excluded tag" in result.reason

    def test_importance_threshold_filters(self, db):
        tag = _tag(db, "Power Grid", "pg-thresh")
        feed = self._feed(db, "pg-high-bar", min_importance_score=95.0)
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))
        db.flush()
        story = self._story_with_tag(db, "pgt", tag)

        rules = load_rules(db, feed)
        result = match_story(db, feed, rules, story)
        assert result.matches is False
        assert "below threshold" in result.reason

    def test_publishing_is_idempotent(self, db):
        tag = _tag(db, "Power Grid", "pg-idem")
        feed = self._feed(db, "pg-idem-feed")
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))
        db.flush()
        story = self._story_with_tag(db, "pgi", tag)

        publish_story(db, story, now=NOW)
        publish_story(db, story, now=NOW)
        assert db.query(FeedInstanceEntry).filter_by(feed_instance_id=feed.id).count() == 1

    def test_a_story_that_stops_matching_is_removed(self, db):
        tag = _tag(db, "Power Grid", "pg-remove")
        feed = self._feed(db, "pg-remove-feed")
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))
        db.flush()
        story = self._story_with_tag(db, "pgr", tag)
        publish_story(db, story, now=NOW)

        story.is_hidden = True
        db.flush()
        publish_story(db, story, now=NOW)
        assert db.query(FeedInstanceEntry).filter_by(feed_instance_id=feed.id).count() == 0

    def test_feed_with_no_rules_takes_everything(self, db):
        feed = self._feed(db, "everything")
        tag = _tag(db, "Anything", "anything")
        story = self._story_with_tag(db, "any", tag)
        placed = publish_story(db, story, now=NOW)
        assert feed.id in [feed_id for feed_id, _ in placed]

    def test_reading_a_feed_is_one_ordered_query(self, db):
        feed = self._feed(db, "read-feed", sort_order="trending")
        tag = _tag(db, "Power Grid", "pg-read")
        db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))
        db.flush()
        story = self._story_with_tag(db, "pgread", tag)
        publish_story(db, story, now=NOW)

        stories = feed_stories(db, feed)
        assert [s.id for s in stories] == [story.id]
