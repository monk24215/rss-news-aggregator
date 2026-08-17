"""Parsing RSS and Atom into normalized items — §11 steps 1-9.

`feedparser` handles the format zoo (RSS 0.9x/1.0/2.0, Atom 0.3/1.0, and the many feeds
that are almost-but-not-quite valid). This module's job is the mapping that follows:
pick the right field out of several plausible ones, and hand back something the ingest
loop can insert without further thought.

Feeds lie, omit, and malform constantly. Every accessor here tolerates a missing field
rather than raising, because one bad item should cost us that item, not the whole fetch.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any

import feedparser

from app.services.normalize import (
    NormalizedItem,
    content_hash,
    normalize_title,
    normalize_url,
    strip_html,
    to_utc,
)


def _first(entry: Any, *names: str) -> Any:
    for name in names:
        value = entry.get(name) if hasattr(entry, "get") else None
        if value:
            return value
    return None


def _parse_time(entry: Any) -> datetime | None:
    """Prefer publication time; fall back to update time.

    feedparser gives a `time.struct_time` in UTC; `calendar.timegm` is the correct
    inverse for that (not `mktime`, which would apply the local timezone).
    """
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(field) if hasattr(entry, "get") else None
        if parsed:
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _extract_image(entry: Any) -> str | None:
    """Find a usable image across the several conventions feeds use."""
    media = entry.get("media_content") or entry.get("media_thumbnail") or []
    if isinstance(media, list):
        for item in media:
            url = (item or {}).get("url")
            if url:
                return url

    for enclosure in entry.get("enclosures") or []:
        enc = enclosure or {}
        if str(enc.get("type", "")).startswith("image/") and enc.get("href"):
            return enc["href"]

    for link in entry.get("links") or []:
        lk = link or {}
        if lk.get("rel") == "enclosure" and str(lk.get("type", "")).startswith("image/"):
            return lk.get("href")

    image = entry.get("image")
    if isinstance(image, dict) and image.get("href"):
        return image["href"]
    return None


def _extract_author(entry: Any) -> str | None:
    author = _first(entry, "author", "creator", "dc_creator")
    if isinstance(author, str):
        return author.strip()[:300] or None
    detail = entry.get("author_detail") or {}
    name = detail.get("name") if isinstance(detail, dict) else None
    return (name or "").strip()[:300] or None


def _extract_description(entry: Any) -> str | None:
    """The feed's own summary — never the article body (Non-Negotiable #6).

    `content` is checked only when `summary` is absent, and the result is truncated:
    some publishers put the entire article in the feed, and storing that would be
    reproducing the work rather than pointing at it.
    """
    summary = _first(entry, "summary", "description", "subtitle")
    if not summary:
        content = entry.get("content") or []
        if isinstance(content, list) and content:
            summary = (content[0] or {}).get("value")
    text = strip_html(summary if isinstance(summary, str) else None)
    return text[:2000] if text else None


def _extract_canonical(entry: Any) -> str | None:
    for link in entry.get("links") or []:
        lk = link or {}
        if lk.get("rel") in ("canonical", "alternate") and lk.get("href"):
            return lk["href"]
    return None


def _entry_url(entry: Any) -> str:
    """The article's URL.

    `id` is only accepted as a fallback when it actually looks like a URL: Atom ids and
    RSS guids are frequently opaque strings ("tag:example.com,2026:abc"), and storing one
    as the link would produce an article that cannot be clicked through to the publisher
    — which Non-Negotiable #2 forbids.
    """
    link = (_first(entry, "link") or "").strip()
    if link:
        return link
    candidate = (_first(entry, "id") or "").strip()
    return candidate if candidate.lower().startswith(("http://", "https://")) else ""


def parse_entry(entry: Any) -> NormalizedItem:
    """Map one feedparser entry to a NormalizedItem."""
    url = _entry_url(entry)
    canonical = _extract_canonical(entry)
    headline = strip_html(_first(entry, "title")) or ""
    description = _extract_description(entry)

    guid = entry.get("id") or entry.get("guid") or None
    if isinstance(guid, dict):
        guid = guid.get("value")
    guid = (str(guid).strip() or None) if guid else None

    # Prefer the canonical link for the comparable form: it is the publisher's own
    # statement about which URL is the article (§11 step 3).
    normalized = normalize_url(canonical or url)

    return NormalizedItem(
        guid=guid[:500] if guid else None,
        url=url,
        canonical_url=canonical,
        headline=headline[:1000],
        description=description,
        author=_extract_author(entry),
        image_url=_extract_image(entry),
        published_at=to_utc(_parse_time(entry)),
        payload=None,
        normalized_url=normalized,
        normalized_title=normalize_title(headline),
        content_hash=content_hash(headline, description, canonical or url),
    )


class ParsedFeed:
    """The result of parsing one feed document."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.items: list[NormalizedItem] = []
        self.parse_error: str | None = None
        #: feedparser reports the detected format ("rss20", "atom10", …) and leaves this
        #: empty for anything that is not a feed at all — an HTML error page, say. An
        #: empty feed is legitimate; a non-feed is a broken source (§35.3).
        self.format: str = getattr(raw, "version", "") or ""

        # feedparser sets `bozo` for malformed documents but often still yields usable
        # entries. We record the complaint and keep whatever parsed — a feed with a
        # stray ampersand should not cost us the day's news.
        if getattr(raw, "bozo", 0):
            exc = getattr(raw, "bozo_exception", None)
            if exc is not None:
                self.parse_error = f"{type(exc).__name__}: {exc}"[:500]

        for entry in getattr(raw, "entries", []) or []:
            try:
                item = parse_entry(entry)
            except Exception as exc:  # one bad item must not lose the rest
                self.parse_error = self.parse_error or f"entry error: {exc}"[:500]
                continue
            if item.is_usable:
                self.items.append(item)

    @property
    def is_feed(self) -> bool:
        """True when the document parsed as a recognizable feed format."""
        return bool(self.format)

    @property
    def feed_title(self) -> str | None:
        feed = getattr(self._raw, "feed", None) or {}
        title = feed.get("title") if hasattr(feed, "get") else None
        return strip_html(title)

    @property
    def feed_description(self) -> str | None:
        feed = getattr(self._raw, "feed", None) or {}
        value = feed.get("subtitle") or feed.get("description") if hasattr(feed, "get") else None
        return strip_html(value)

    @property
    def feed_link(self) -> str | None:
        feed = getattr(self._raw, "feed", None) or {}
        return (feed.get("link") if hasattr(feed, "get") else None) or None

    @property
    def feed_image(self) -> str | None:
        feed = getattr(self._raw, "feed", None) or {}
        image = feed.get("image") if hasattr(feed, "get") else None
        if isinstance(image, dict):
            return image.get("href") or image.get("url")
        return None


def parse(document: str | bytes) -> ParsedFeed:
    """Parse a feed document (RSS or Atom) into normalized items."""
    return ParsedFeed(feedparser.parse(document))
