"""The administrative interface — §35, §29.3, §36, §37.

The dashboard's job is triage: §35.1 says what needs attention comes before the
counters, so the tests check that a failing source and a queued review item actually
surface there rather than merely existing in a table somewhere.

Every action is checked for its audit trail too. An editorial change that leaves no
record is the thing §37 exists to prevent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import (
    Article,
    AuditLog,
    ChangeHistory,
    FeedInstance,
    ReviewQueueItem,
    Source,
    SourceFetchLog,
    Story,
)
from app.services.cluster import assign_story
from app.services.scoring import rescore

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def newsroom(db):
    healthy = Source(name="Healthy Wire", slug="healthy", feed_url="https://h.example/rss")
    failing = Source(
        name="Broken Feed",
        slug="broken",
        feed_url="https://b.example/rss",
        consecutive_failures=4,
        last_error="HTTP 403",
    )
    db.add_all([healthy, failing])
    db.flush()

    article = Article(
        source_id=healthy.id,
        original_guid="admin-1",
        original_url="https://h.example/outage",
        original_headline="Dominion Energy outage hits Chesterfield County",
        original_description="About 50,000 customers lost power.",
        original_published_at=NOW,
    )
    db.add(article)
    db.flush()
    story = db.get(Story, assign_story(db, article, now=NOW).story_id)
    rescore(db, story, now=NOW)

    db.add(
        ReviewQueueItem(
            reason="story_merge", story_id=story.id, confidence=0.55,
            detail={"rationale": "shared: dominion energy"},
        )
    )
    db.add(FeedInstance(name="Empty Feed", slug="empty-feed"))
    db.add(
        SourceFetchLog(
            source_id=failing.id, attempted_at=NOW, succeeded=False,
            http_status=403, error="HTTP 403",
        )
    )
    db.flush()
    return {"healthy": healthy, "failing": failing, "story": story}


# --- Dashboard ---------------------------------------------------------------


def test_dashboard_leads_with_what_needs_attention(client, newsroom):
    """§35.1 — triage before counters."""
    body = client.get("/admin").text
    attention = body.index("Needs attention")
    activity = body.index("Last 24 hours")
    assert attention < activity

    assert "Broken Feed" in body           # failing source
    assert "story merge" in body           # queued review item
    assert "Empty Feed" in body            # a feed matching nothing


def test_dashboard_says_so_when_nothing_is_wrong(client, db):
    body = client.get("/admin").text
    assert "Nothing needs attention" in body


def test_dashboard_shows_activity_and_spend(client, newsroom):
    body = client.get("/admin").text
    assert "AI spend, 24h" in body
    assert "New stories" in body


# --- Sources -----------------------------------------------------------------


def test_sources_list_puts_failures_first(client, newsroom):
    body = client.get("/admin/sources").text
    assert body.index("Broken Feed") < body.index("Healthy Wire")
    assert "4 failures" in body


def test_source_detail_shows_fetch_history(client, newsroom):
    body = client.get(f"/admin/source/{newsroom['failing'].id}").text
    assert "HTTP 403" in body
    assert "Fetch history" in body


def test_toggling_a_source_is_audited(client, db, newsroom):
    """§37 — the change is traceable; §36 — the old value is recoverable."""
    source = newsroom["healthy"]
    client.post(f"/admin/source/{source.id}/toggle", follow_redirects=False)

    db.expire_all()
    assert db.get(Source, source.id).is_active is False

    entry = db.query(AuditLog).filter_by(entity_type="source", entity_id=source.id).one()
    assert "Deactivated Healthy Wire" in entry.summary

    change = db.get(ChangeHistory, entry.change_id)
    assert change.previous_value == {"value": True}
    assert change.new_value == {"value": False}


def test_clearing_failures_lets_the_source_retry(client, db, newsroom):
    source = newsroom["failing"]
    source.disabled_until = NOW + timedelta(hours=6)
    db.flush()

    client.post(f"/admin/source/{source.id}/clear-failures", follow_redirects=False)
    db.expire_all()
    refreshed = db.get(Source, source.id)
    assert refreshed.consecutive_failures == 0
    assert refreshed.disabled_until is None


# --- Review queue ------------------------------------------------------------


def test_review_queue_shows_the_story_behind_each_item(client, newsroom):
    body = client.get("/admin/review").text
    assert "story merge" in body
    assert "Dominion Energy outage" in body
    assert "shared: dominion energy" in body


def test_resolving_a_review_item_records_the_decision(client, db, newsroom):
    item = db.query(ReviewQueueItem).one()
    client.post(
        f"/admin/review/{item.id}/resolve", data={"decision": "approved"}, follow_redirects=False
    )
    db.expire_all()
    refreshed = db.get(ReviewQueueItem, item.id)
    assert refreshed.state == "approved" and refreshed.resolved_at is not None
    assert db.query(AuditLog).filter_by(action="review.resolve").count() == 1


def test_empty_review_queue_is_not_an_error(client, db):
    assert "Nothing is waiting for review" in client.get("/admin/review").text


# --- Editorial actions -------------------------------------------------------


def test_hiding_a_story_removes_it_from_the_public_site(client, db, newsroom):
    """§35.4 with Non-Negotiable #5 — a human decision the automation must respect."""
    story = newsroom["story"]
    assert client.get(f"/story/{story.slug}").status_code == 200

    client.post(f"/admin/story/{story.id}/hide", follow_redirects=False)
    db.expire_all()
    assert db.get(Story, story.id).is_hidden is True
    assert db.get(Story, story.id).has_manual_override is True
    assert client.get(f"/story/{story.slug}").status_code == 404


def test_locking_a_story_is_audited(client, db, newsroom):
    story = newsroom["story"]
    client.post(f"/admin/story/{story.id}/lock", follow_redirects=False)
    db.expire_all()
    assert db.get(Story, story.id).is_locked is True
    assert db.query(AuditLog).filter_by(action="story.lock").count() == 1


# --- Feeds and audit ---------------------------------------------------------


def test_feed_screen_shows_configuration_beside_output(client, newsroom):
    """§29.3 — a rule that matches nothing should be obvious."""
    body = client.get("/admin/feeds").text
    assert "Empty Feed" in body
    assert "currently matches no stories" in body


def test_audit_page_reads_in_human_terms(client, db, newsroom):
    client.post(f"/admin/story/{newsroom['story'].id}/hide", follow_redirects=False)
    body = client.get("/admin/audit").text
    assert "Hid story" in body


def test_stories_screen_lists_editorial_controls(client, newsroom):
    body = client.get("/admin/stories").text
    assert "Hide" in body and "Lock" in body
