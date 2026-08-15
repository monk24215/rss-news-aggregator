"""The reader-facing site — §30, §34.

Two rules are worth testing at the page level rather than trusting to templates:

  - every story links back to the publisher (Non-Negotiable #2), and
  - nothing generated is displayed without a label (Non-Negotiable #3).

Both are the kind of thing that silently breaks during a redesign, which is exactly why
they are assertions rather than review comments.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import (
    AIResult,
    AIResultInput,
    Article,
    FeedInstance,
    FeedInstanceTag,
    Source,
    Story,
    StoryTag,
    Tag,
)
from app.services.cluster import assign_story
from app.services.feeds import publish_story
from app.services.scoring import rescore

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(db):
    """A test client bound to the rolled-back transaction the `db` fixture owns."""
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def newsroom(db):
    """A small but complete world: one story, four publishers, one feed."""
    tag = Tag(name="Power Grid", slug="power-grid")
    db.add(tag)
    db.flush()

    feed = FeedInstance(name="Top Stories", slug="top-stories", sort_order="trending")
    db.add(feed)
    db.flush()
    db.add(FeedInstanceTag(feed_instance_id=feed.id, tag_id=tag.id, mode="include"))

    story = None
    for i, name in enumerate(["Reuters", "AP", "Local Paper", "Trade Press"]):
        slug = name.lower().replace(" ", "-")
        source = Source(
            name=name, slug=slug, feed_url=f"https://{slug}.example/rss",
            website_url=f"https://{slug}.example", priority=7,
        )
        db.add(source)
        db.flush()
        article = Article(
            source_id=source.id,
            original_guid=f"{slug}-1",
            original_url=f"https://{slug}.example/outage",
            original_headline="Dominion Energy outage hits Chesterfield County",
            original_description="Crews are restoring service across Chesterfield County.",
            original_published_at=NOW + timedelta(minutes=i * 15),
            original_image_url="https://img.example/outage.jpg" if i == 0 else None,
        )
        db.add(article)
        db.flush()
        decision = assign_story(db, article, now=NOW)
        story = db.get(Story, decision.story_id)

    db.add(StoryTag(story_id=story.id, tag_id=tag.id))
    db.flush()
    rescore(db, story, now=NOW + timedelta(hours=1))
    publish_story(db, story, now=NOW + timedelta(hours=1))
    db.flush()
    return {"story": story, "feed": feed, "tag": tag}


# --- Front page --------------------------------------------------------------


def test_front_page_shows_the_story(client, newsroom):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Dominion Energy outage hits Chesterfield County" in resp.text
    assert "4 sources" in resp.text


def test_front_page_names_the_publishers(client, newsroom):
    """§34.2's card carries the source list, not just a count."""
    body = client.get("/").text
    for name in ("Reuters", "AP", "Local Paper", "Trade Press"):
        assert name in body


def test_empty_site_says_so_instead_of_breaking(client, db):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No stories yet" in resp.text


# --- Story page --------------------------------------------------------------


def test_story_page_lists_every_source_article_with_a_working_link(client, newsroom):
    """Non-Negotiable #2 — every displayed fact traces to a publisher's URL."""
    story = newsroom["story"]
    resp = client.get(f"/story/{story.slug}")
    assert resp.status_code == 200
    for slug in ("reuters", "ap", "local-paper", "trade-press"):
        assert f"https://{slug}.example/outage" in resp.text


def test_story_page_shows_timeline_and_reasoning(client, newsroom):
    resp = client.get(f"/story/{newsroom['story'].slug}")
    assert "Timeline" in resp.text
    assert "Why this is here" in resp.text
    assert "independent source" in resp.text


def test_story_page_marks_the_first_report(client, newsroom):
    assert "first reported" in client.get(f"/story/{newsroom['story'].slug}").text


def test_missing_story_is_a_404(client, newsroom):
    assert client.get("/story/does-not-exist").status_code == 404


def test_hidden_story_is_not_readable(client, db, newsroom):
    newsroom["story"].is_hidden = True
    db.flush()
    assert client.get(f"/story/{newsroom['story'].slug}").status_code == 404


# --- AI labelling ------------------------------------------------------------


def test_without_ai_nothing_claims_to_be_generated(client, newsroom):
    """v1 runs with no API key: the page must be honest, not empty."""
    body = client.get(f"/story/{newsroom['story'].slug}").text
    assert "AI summary" not in body
    assert "Crews are restoring service" in body


def test_ai_summary_is_always_labelled(client, db, newsroom):
    """Non-Negotiable #3 — generated text is never shown as reporting."""
    story = newsroom["story"]
    result = AIResult(
        target_type="story",
        story_id=story.id,
        operation="generate_summary",
        content="Four outlets report a regional outage affecting Chesterfield County.",
        provider="anthropic",
        model="claude-x",
        generated_at=NOW,
    )
    db.add(result)
    db.flush()
    for article in story.articles:
        db.add(AIResultInput(ai_result_id=result.id, article_id=article.id))
    db.flush()

    body = client.get(f"/story/{story.slug}").text
    assert "Four outlets report a regional outage" in body
    assert "AI summary" in body
    assert "generated from 4 source reports" in body
    # …and the original reporting is still listed underneath.
    assert "https://reuters.example/outage" in body


def test_an_edited_summary_says_so(client, db, newsroom):
    story = newsroom["story"]
    db.add(
        AIResult(
            target_type="story",
            story_id=story.id,
            operation="generate_summary",
            content="Machine text.",
            edited_content="An editor rewrote this summary.",
            provider="anthropic",
            model="claude-x",
            generated_at=NOW,
        )
    )
    db.flush()
    body = client.get(f"/story/{story.slug}").text
    assert "An editor rewrote this summary." in body
    assert "AI summary, edited" in body
    assert "Machine text." not in body


# --- Browse ------------------------------------------------------------------


def test_topics_page(client, newsroom):
    resp = client.get("/topics")
    assert resp.status_code == 200 and "Power Grid" in resp.text


def test_topic_page_lists_its_stories(client, newsroom):
    resp = client.get("/topic/power-grid")
    assert resp.status_code == 200
    assert "Dominion Energy outage" in resp.text


def test_sources_page_credits_publishers(client, newsroom):
    """§34.6 — the publications are named and linked."""
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Reuters" in resp.text
    assert "https://reuters.example" in resp.text


def test_feed_page(client, newsroom):
    resp = client.get("/feed/top-stories")
    assert resp.status_code == 200
    assert "Dominion Energy outage" in resp.text


def test_search_finds_by_headline(client, newsroom):
    resp = client.get("/search", params={"q": "Chesterfield"})
    assert resp.status_code == 200
    assert "Dominion Energy outage" in resp.text


def test_search_with_no_query_is_not_an_error(client, newsroom):
    assert client.get("/search").status_code == 200


# --- RSS output --------------------------------------------------------------


def test_rss_is_valid_xml_with_our_headline_and_their_link(client, newsroom):
    """§30 — our presentation, their URL."""
    resp = client.get("/rss/top-stories")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["content-type"]

    root = ET.fromstring(resp.text)
    channel = root.find("channel")
    assert channel.findtext("title") == "Top Stories"

    items = channel.findall("item")
    assert len(items) == 1
    item = items[0]
    assert "Dominion Energy outage" in item.findtext("title")
    # The link is the publisher's, not ours.
    assert item.findtext("link").startswith("https://reuters.example/")
    assert "Reported by: AP · Local Paper · Reuters · Trade Press" in item.findtext("description")
    assert item.findtext("category") == "Power Grid"


def test_rss_survives_an_ampersand_in_a_headline(client, db, newsroom):
    """The failure other people's feeds hand us; ours must not hand it to anyone else."""
    source = Source(name="Amp", slug="amp", feed_url="https://amp.example/rss")
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id,
        original_guid="amp-1",
        original_url="https://amp.example/rates",
        original_headline="Rates rise & markets react <fast>",
        original_published_at=NOW,
    )
    db.add(article)
    db.flush()
    decision = assign_story(db, article, now=NOW)
    story = db.get(Story, decision.story_id)
    db.add(StoryTag(story_id=story.id, tag_id=newsroom["tag"].id))
    db.flush()
    rescore(db, story, now=NOW)
    publish_story(db, story, now=NOW)
    db.flush()

    resp = client.get("/rss/top-stories")
    root = ET.fromstring(resp.text)  # raises if we emitted malformed XML
    titles = [item.findtext("title") for item in root.find("channel").findall("item")]
    assert "Rates rise & markets react <fast>" in titles


def test_disabled_rss_is_a_404(client, db, newsroom):
    newsroom["feed"].rss_enabled = False
    db.flush()
    assert client.get("/rss/top-stories").status_code == 404
