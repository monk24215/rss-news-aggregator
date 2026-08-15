"""Ingestion — §11, §12, §13, §35.3.

HTTP is mocked with `httpx.MockTransport`, which exercises the real client, real
headers, and real status handling without touching the network. The database is real,
because the dedup guarantees are half schema and half code and testing only one half
would prove the wrong thing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import Article, Source, SourceFetchLog
from app.services import feedparse
from app.services.dedupe import find_duplicate
from app.services.ingest import _backoff_for, due_sources, ingest_source
from tests.fixtures import feeds

pytestmark = pytest.mark.usefixtures("schema")

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _serving(body: str, status: int = 200, headers: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers=headers or {})

    return handler


def _source(db, slug="wire", **kw) -> Source:
    src = Source(
        name=slug.title(), slug=slug, feed_url=f"https://{slug}.example/rss", **kw
    )
    db.add(src)
    db.flush()
    return src


# --- Parsing -----------------------------------------------------------------


class TestParsing:
    def test_rss_items(self):
        parsed = feedparse.parse(feeds.RSS_BASIC)
        assert len(parsed.items) == 2
        first = parsed.items[0]
        assert first.headline == "Power outage hits 50,000 customers"
        assert first.guid == "wire-0001"
        assert first.published_at == datetime(2026, 8, 11, 8, 12, tzinfo=timezone.utc)
        assert first.image_url == "https://wire.example/img/outage.jpg"
        assert first.description == "A regional outage began early Tuesday."

    def test_tracking_parameters_do_not_reach_the_normalized_url(self):
        item = feedparse.parse(feeds.RSS_BASIC).items[0]
        assert "utm_source" not in (item.normalized_url or "")
        assert item.normalized_url == "wire.example/outage?id=1"
        # …while the original URL is preserved exactly as published (Layer 1).
        assert "utm_source=rss" in item.url

    def test_atom_items(self):
        parsed = feedparse.parse(feeds.ATOM_BASIC)
        assert len(parsed.items) == 1
        item = parsed.items[0]
        assert item.headline == "Storm knocks out power across the region"
        assert item.author == "T Writer"
        assert item.published_at == datetime(2026, 8, 11, 8, 27, tzinfo=timezone.utc)
        assert item.normalized_url == "times.example/storm-power"

    def test_feed_metadata_for_the_onboarding_preview(self):
        """§35.2's wizard previews name and description before activation."""
        parsed = feedparse.parse(feeds.ATOM_BASIC)
        assert parsed.feed_title == "Example Times"
        assert parsed.feed_description == "A newspaper"

    def test_unattributable_items_are_dropped(self):
        """No link or no headline means no way to credit the publisher."""
        parsed = feedparse.parse(feeds.RSS_UNUSABLE_ITEMS)
        assert [i.headline for i in parsed.items] == ["This one is fine"]

    def test_malformed_feed_still_yields_articles(self):
        """A stray ampersand should cost a complaint, not the day's news."""
        parsed = feedparse.parse(feeds.RSS_MALFORMED_BUT_USABLE)
        assert len(parsed.items) == 1
        assert parsed.parse_error is not None

    def test_html_page_yields_nothing(self):
        assert feedparse.parse(feeds.NOT_A_FEED).items == []


# --- Deduplication -----------------------------------------------------------


class TestDedupe:
    def test_guid_match(self, db):
        src = _source(db, "dedup1")
        item = feedparse.parse(feeds.RSS_BASIC).items[0]
        assert find_duplicate(db, src.id, item).is_duplicate is False

        db.add(
            Article(
                source_id=src.id,
                original_guid=item.guid,
                original_url=item.url,
                original_headline=item.headline,
            )
        )
        db.flush()
        verdict = find_duplicate(db, src.id, item)
        assert verdict.is_duplicate and verdict.signal == "guid"

    def test_normalized_url_match_when_guid_differs(self, db):
        """The §13 case: same story, new GUID, different campaign parameter."""
        src = _source(db, "dedup2")
        first = feedparse.parse(feeds.RSS_BASIC).items[0]
        db.add(
            Article(
                source_id=src.id,
                original_guid=first.guid,
                original_url=first.url,
                original_headline=first.headline,
                normalized_url=first.normalized_url,
                content_hash=first.content_hash,
            )
        )
        db.flush()

        repost = feedparse.parse(feeds.RSS_SECOND_FETCH).items[0]
        assert repost.guid != first.guid
        verdict = find_duplicate(db, src.id, repost)
        assert verdict.is_duplicate
        assert verdict.signal in {"normalized_url", "content_hash"}

    def test_two_outlets_covering_one_event_are_not_duplicates(self, db):
        """The distinction the whole product rests on (§3, §20).

        If these collapsed into one article we would lose the source count that makes a
        story worth reading.
        """
        wire = _source(db, "wire-x")
        times = _source(db, "times-x")
        wire_item = feedparse.parse(feeds.RSS_BASIC).items[0]
        times_item = feedparse.parse(feeds.ATOM_BASIC).items[0]

        db.add(
            Article(
                source_id=wire.id,
                original_guid=wire_item.guid,
                original_url=wire_item.url,
                original_headline=wire_item.headline,
                normalized_url=wire_item.normalized_url,
                content_hash=wire_item.content_hash,
            )
        )
        db.flush()
        assert find_duplicate(db, times.id, times_item).is_duplicate is False


# --- Fetching ----------------------------------------------------------------


class TestFetch:
    def test_first_fetch_stores_articles(self, db):
        src = _source(db, "fetch1")
        report = ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)

        assert report.ok and report.items_new == 2 and report.items_duplicate == 0
        stored = db.query(Article).filter_by(source_id=src.id).all()
        assert {a.original_headline for a in stored} == {
            "Power outage hits 50,000 customers",
            "BREAKING: Utility crews dispatched",
        }
        # Original headline is kept verbatim, decorations and all; only the derived
        # comparison form is cleaned up.
        breaking = next(a for a in stored if a.original_headline.startswith("BREAKING"))
        assert breaking.normalized_title == "utility crews dispatched"

    def test_second_fetch_adds_only_what_is_new(self, db):
        src = _source(db, "fetch2")
        ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        report = ingest_source(
            db, src, client=_client(_serving(feeds.RSS_SECOND_FETCH)), now=NOW
        )

        assert report.items_new == 1
        assert report.items_duplicate == 2
        assert db.query(Article).filter_by(source_id=src.id).count() == 3

    def test_running_the_same_fetch_twice_is_harmless(self, db):
        """Non-Negotiable #8 — every stage must be safe to retry."""
        src = _source(db, "fetch-retry")
        ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        again = ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        assert again.items_new == 0
        assert db.query(Article).filter_by(source_id=src.id).count() == 2

    def test_conditional_request_headers_are_sent_and_stored(self, db):
        """§12 politeness: remember validators, send them next time."""
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(
                200,
                text=feeds.RSS_BASIC,
                headers={"ETag": 'W/"abc"', "Last-Modified": "Tue, 11 Aug 2026 08:00:00 GMT"},
            )

        src = _source(db, "cond")
        ingest_source(db, src, client=_client(handler), now=NOW)
        assert src.http_etag == 'W/"abc"'
        assert "user-agent" in seen and "AIRSSNewsAggregator" in seen["user-agent"]

        ingest_source(db, src, client=_client(handler), now=NOW)
        assert seen.get("if-none-match") == 'W/"abc"'
        assert seen.get("if-modified-since") == "Tue, 11 Aug 2026 08:00:00 GMT"

    def test_not_modified_is_a_cheap_success(self, db):
        src = _source(db, "cond304", http_etag='W/"abc"')
        report = ingest_source(db, src, client=_client(_serving("", status=304)), now=NOW)
        assert report.ok and report.not_modified and report.items_new == 0
        assert src.consecutive_failures == 0

    def test_http_error_is_recorded_not_raised(self, db):
        src = _source(db, "err500")
        report = ingest_source(db, src, client=_client(_serving("", status=500)), now=NOW)
        assert report.ok is False and report.error == "HTTP 500"
        assert src.consecutive_failures == 1
        assert src.last_error == "HTTP 500"

    def test_network_error_is_recorded_not_raised(self, db):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("name resolution failed")

        src = _source(db, "errdns")
        report = ingest_source(db, src, client=_client(handler), now=NOW)
        assert report.ok is False and "ConnectError" in report.error
        assert src.consecutive_failures == 1

    def test_repeated_failure_rests_the_source(self, db):
        """§12 — stop hammering a feed that keeps failing."""
        src = _source(db, "failing", consecutive_failures=4)
        ingest_source(db, src, client=_client(_serving("", status=503)), now=NOW)
        assert src.consecutive_failures == 5
        assert src.disabled_until is not None and src.disabled_until > NOW

    def test_success_clears_the_rest_period(self, db):
        src = _source(
            db, "recovered", consecutive_failures=7, disabled_until=NOW + timedelta(hours=1)
        )
        ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        assert src.consecutive_failures == 0
        assert src.disabled_until is None

    def test_backoff_grows_and_is_capped(self):
        assert _backoff_for(1) == timedelta(0)
        assert _backoff_for(5) > timedelta(0)
        assert _backoff_for(6) > _backoff_for(5)
        assert _backoff_for(100) <= timedelta(hours=12)

    def test_item_cap_is_respected(self, db):
        src = _source(db, "capped", max_items_per_fetch=1)
        report = ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        assert report.items_new == 1

    def test_every_attempt_is_logged(self, db):
        """§35.3 — source health is a record, not a guess."""
        src = _source(db, "logged")
        ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        ingest_source(db, src, client=_client(_serving("", status=500)), now=NOW)

        logs = db.query(SourceFetchLog).filter_by(source_id=src.id).all()
        assert len(logs) == 2
        assert {log.succeeded for log in logs} == {True, False}
        ok_log = next(log for log in logs if log.succeeded)
        assert ok_log.items_returned == 2 and ok_log.items_new == 2

    def test_garbage_response_fails_cleanly(self, db):
        src = _source(db, "garbage")
        report = ingest_source(db, src, client=_client(_serving(feeds.NOT_A_FEED)), now=NOW)
        assert report.ok is False
        assert src.consecutive_failures == 1

    def test_article_count_tracks_stored_items(self, db):
        src = _source(db, "counted")
        ingest_source(db, src, client=_client(_serving(feeds.RSS_BASIC)), now=NOW)
        assert src.article_count == 2


# --- Scheduling --------------------------------------------------------------


class TestDueSources:
    def test_never_fetched_is_due(self, db):
        src = _source(db, "due-new")
        assert src.id in due_sources(db, now=NOW)

    def test_recently_fetched_is_not_due(self, db):
        src = _source(
            db, "due-recent", fetch_interval_seconds=900,
            last_fetch_attempted_at=NOW - timedelta(minutes=5),
        )
        assert src.id not in due_sources(db, now=NOW)

    def test_elapsed_interval_is_due(self, db):
        src = _source(
            db, "due-old", fetch_interval_seconds=900,
            last_fetch_attempted_at=NOW - timedelta(minutes=20),
        )
        assert src.id in due_sources(db, now=NOW)

    def test_inactive_source_is_never_due(self, db):
        src = _source(db, "due-off", is_active=False)
        assert src.id not in due_sources(db, now=NOW)

    def test_rested_source_is_skipped_until_its_time(self, db):
        src = _source(db, "due-rested", disabled_until=NOW + timedelta(hours=1))
        assert src.id not in due_sources(db, now=NOW)
        assert src.id in due_sources(db, now=NOW + timedelta(hours=2))
