"""Fetching and storing feed items — §11, §12, §14, §35.3.

One source, one fetch, one transaction. The function returns a report rather than
logging and forgetting, so the caller (a worker task) can record it, and so tests can
assert on what happened rather than on side effects.

The politeness rules in §12 are implemented here rather than left to the scheduler:
conditional requests, timeouts, backoff, item caps, a real user-agent, and temporary
disablement after repeated failures. A publisher whose feed we poll should not be able
to tell us apart from a well-behaved reader.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import Article, Source, SourceFetchLog
from app.services import feedparse
from app.services.dedupe import find_duplicate
from app.services.normalize import NormalizedItem

logger = logging.getLogger("ingest")

USER_AGENT = (
    "AIRSSNewsAggregator/0.1 (+https://newsregator.wiredhowse.app; feed reader; "
    "contact via site)"
)

#: After this many consecutive failures a source is rested rather than hammered (§12).
FAILURE_THRESHOLD = 5
#: How long to rest it. Doubles with each further failure, capped.
BASE_BACKOFF = timedelta(minutes=15)
MAX_BACKOFF = timedelta(hours=12)


@dataclass
class FetchReport:
    """What one fetch of one source did."""

    source_id: int
    ok: bool = False
    http_status: int | None = None
    response_ms: int | None = None
    not_modified: bool = False
    items_seen: int = 0
    items_new: int = 0
    items_duplicate: int = 0
    items_unusable: int = 0
    error: str | None = None
    parse_error: str | None = None
    new_article_ids: list[int] = field(default_factory=list)

    def summary(self) -> str:
        if self.not_modified:
            return f"source={self.source_id} 304 not modified"
        if not self.ok:
            return f"source={self.source_id} FAILED {self.error}"
        return (
            f"source={self.source_id} ok status={self.http_status} "
            f"seen={self.items_seen} new={self.items_new} dup={self.items_duplicate}"
        )


def _backoff_for(consecutive_failures: int) -> timedelta:
    """Exponential backoff, capped (§12)."""
    if consecutive_failures < FAILURE_THRESHOLD:
        return timedelta(0)
    over = consecutive_failures - FAILURE_THRESHOLD
    delay = BASE_BACKOFF * (2**min(over, 8))
    return min(delay, MAX_BACKOFF)


def due_sources(session: Session, now: datetime | None = None, limit: int = 200) -> list[int]:
    """Ids of active sources whose fetch interval has elapsed (§12, §43.2).

    The scheduler calls this and enqueues one job per source; it does no fetching itself.
    """
    now = now or datetime.now(timezone.utc)
    rows = session.query(Source.id, Source.last_fetch_attempted_at, Source.fetch_interval_seconds,
                         Source.disabled_until).filter(Source.is_active.is_(True)).all()
    due: list[int] = []
    for source_id, last_attempt, interval, disabled_until in rows:
        if disabled_until and disabled_until > now:
            continue
        if last_attempt is None:
            due.append(source_id)
            continue
        if last_attempt + timedelta(seconds=interval or 900) <= now:
            due.append(source_id)
    return due[:limit]


def fetch_document(
    source: Source, client: httpx.Client
) -> tuple[httpx.Response | None, str | None]:
    """Perform the conditional HTTP request (§12).

    Returns (response, error). A 304 is a success with no body — the cheapest possible
    outcome and the reason conditional requests are worth the bookkeeping.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml,"
                                                   " application/xml;q=0.9, */*;q=0.8"}
    if source.http_etag:
        headers["If-None-Match"] = source.http_etag
    if source.http_last_modified:
        headers["If-Modified-Since"] = source.http_last_modified
    try:
        response = client.get(
            source.feed_url,
            headers=headers,
            timeout=source.request_timeout_seconds or 20,
            follow_redirects=True,
        )
        return response, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]


def _article_from_item(source_id: int, item: NormalizedItem, fetched_at: datetime) -> Article:
    """Build the row. Original fields are written once here and never again (§4.2)."""
    return Article(
        source_id=source_id,
        original_guid=item.guid,
        original_url=item.url,
        original_canonical_url=item.canonical_url,
        original_headline=item.headline,
        original_description=item.description,
        original_author=item.author,
        original_image_url=item.image_url,
        original_published_at=item.published_at,
        normalized_url=item.normalized_url,
        normalized_title=item.normalized_title,
        content_hash=item.content_hash,
        fetched_at=fetched_at,
    )


def ingest_source(
    session: Session,
    source: Source,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> FetchReport:
    """Fetch one source and store any new items. Returns a report; does not commit.

    The caller commits, so a fetch and its health update land atomically.
    """
    now = now or datetime.now(timezone.utc)
    report = FetchReport(source_id=source.id)
    owns_client = client is None
    client = client or httpx.Client()

    started = datetime.now(timezone.utc)
    try:
        response, error = fetch_document(source, client)
        report.response_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        source.last_fetch_attempted_at = now

        if error is not None:
            report.error = error
            _record_failure(session, source, report, now)
            return report

        report.http_status = response.status_code

        if response.status_code == 304:
            report.ok = True
            report.not_modified = True
            _record_success(session, source, report, now)
            return report

        if response.status_code >= 400:
            report.error = f"HTTP {response.status_code}"
            _record_failure(session, source, report, now)
            return report

        parsed = feedparse.parse(response.content)
        report.parse_error = parsed.parse_error
        report.items_seen = len(parsed.items)

        # An empty feed is fine — publishers have quiet days. A response that is not a
        # feed at all is a broken source, and saying so is the point of §35.3.
        if not parsed.is_feed:
            detail = parsed.parse_error or "response was not RSS or Atom"
            report.error = f"unparseable feed: {detail}"
            _record_failure(session, source, report, now)
            return report

        cap = source.max_items_per_fetch or 100
        for item in parsed.items[:cap]:
            if not item.is_usable:
                report.items_unusable += 1
                continue
            verdict = find_duplicate(session, source.id, item)
            if verdict.is_duplicate:
                report.items_duplicate += 1
                continue
            article = _article_from_item(source.id, item, now)
            session.add(article)
            try:
                session.flush()
            except Exception as exc:
                # The unique constraints are the backstop; losing a race is normal
                # under concurrent fetches and is not an error worth failing the run.
                session.rollback()
                logger.info("skipped item on constraint: %s", exc)
                report.items_duplicate += 1
                continue
            report.items_new += 1
            report.new_article_ids.append(article.id)

        # Remember the validators so the next fetch can be a cheap 304 (§12).
        source.http_etag = response.headers.get("ETag") or source.http_etag
        source.http_last_modified = (
            response.headers.get("Last-Modified") or source.http_last_modified
        )

        report.ok = True
        _record_success(session, source, report, now)
        return report
    finally:
        if owns_client:
            client.close()


def _record_success(session: Session, source: Source, report: FetchReport, now: datetime) -> None:
    source.last_fetch_succeeded_at = now
    source.last_http_status = report.http_status
    source.last_response_ms = report.response_ms
    source.last_error = None
    source.consecutive_failures = 0
    source.disabled_until = None
    source.article_count = (source.article_count or 0) + report.items_new
    session.add(
        SourceFetchLog(
            source_id=source.id,
            attempted_at=now,
            succeeded=True,
            http_status=report.http_status,
            response_ms=report.response_ms,
            items_returned=report.items_seen,
            items_new=report.items_new,
            parse_error=report.parse_error,
            not_modified=report.not_modified,
        )
    )


def _record_failure(session: Session, source: Source, report: FetchReport, now: datetime) -> None:
    source.last_fetch_failed_at = now
    source.last_http_status = report.http_status
    source.last_response_ms = report.response_ms
    source.last_error = report.error
    source.consecutive_failures = (source.consecutive_failures or 0) + 1
    backoff = _backoff_for(source.consecutive_failures)
    if backoff:
        source.disabled_until = now + backoff
        logger.warning(
            "source %s rested until %s after %s consecutive failures",
            source.id,
            source.disabled_until.isoformat(),
            source.consecutive_failures,
        )
    session.add(
        SourceFetchLog(
            source_id=source.id,
            attempted_at=now,
            succeeded=False,
            http_status=report.http_status,
            response_ms=report.response_ms,
            items_returned=report.items_seen or None,
            items_new=0,
            parse_error=report.parse_error,
            error=report.error,
        )
    )
