"""Turning a feed item into a comparable article — §11 steps 2-9, §13.

Deduplication is only as good as normalization. §13's own example is two URLs that
differ by a tracking parameter and must be treated as one article, so the normalizer is
where that judgement lives, isolated and testable, rather than smeared through the
ingest loop.

Everything here is pure: no database, no network. That is deliberate — these are the
rules most likely to need tuning later, and pure functions are the ones you can tune
with confidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Query parameters that identify a campaign, not a document. Stripped before comparing.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "utm_reader",
        "utm_brand",
        "utm_social",
        "utm_social_type",
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "twclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
        "cmpid",
        "cmp",
        "ito",
        "at_medium",
        "at_campaign",
        "sh",
        "smid",
        "partner",
        "spm",
        "yclid",
        "_hsenc",
        "_hsmi",
        "icid",
        "ncid",
        "rss",
        "feed",
    }
)

#: Leading noise publishers put in front of a headline.
_HEADLINE_PREFIXES = re.compile(
    r"^\s*(breaking|update|updated|exclusive|watch|video|live|analysis|opinion|photos)"
    r"\s*[:\-–—]\s*",
    re.IGNORECASE,
)

#: Trailing " - Publication Name" or " | Publication Name" suffixes.
_HEADLINE_SUFFIX = re.compile(r"\s*[\|\-–—]\s*[^\|\-–—]{2,40}\s*$")

_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_APOSTROPHES = re.compile(r"['’ʼ‘`]")
_HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str | None:
    """Remove tags and collapse entities enough for a plain-text description.

    We are not rendering the publisher's HTML — we do not store article bodies
    (Non-Negotiable #6), only the feed's own summary field, and we store it as text.
    """
    if not text:
        return None
    cleaned = _HTML_TAG.sub(" ", text)
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned or None


def normalize_url(url: str | None) -> str | None:
    """Canonical comparable form of a URL (§13).

    - scheme and host lowercased, default ports dropped
    - `www.` dropped
    - tracking parameters removed, remaining parameters sorted
    - fragment removed
    - trailing slash removed (except for a bare root)

    The scheme is dropped from the result entirely: the same article served over http
    and https is the same article, and keeping the scheme would let one publisher's
    protocol upgrade flood the feed with apparent duplicates.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if "//" not in url:
        url = "//" + url

    parts = urlsplit(url if "://" in url else "https:" + url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    query.sort()

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    return urlunsplit(("", host, path, urlencode(query), "")).lstrip("/") or host


def normalize_title(title: str | None) -> str | None:
    """Comparable form of a headline (§13).

    Case, punctuation, accents, and the publisher's own decorations are removed. Two
    outlets running the same wire copy under "BREAKING: Storm hits coast" and
    "Storm hits coast - Example Times" normalize to the same string.
    """
    if not title:
        return None
    text = strip_html(title) or ""
    text = _HEADLINE_PREFIXES.sub("", text)
    text = _HEADLINE_SUFFIX.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Apostrophes are deleted rather than replaced with a space, so a possessive stays
    # one word: "Café's" → "cafes", not "cafe s".
    text = _APOSTROPHES.sub("", text)
    text = _NON_WORD.sub(" ", text.lower())
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def content_hash(title: str | None, description: str | None, url: str | None) -> str:
    """Stable fingerprint of an item's substance (§13).

    Built from the normalized forms so that incidental formatting changes do not produce
    a new hash, and a republished item is recognized as the same one.
    """
    parts = [
        normalize_title(title) or "",
        (strip_html(description) or "")[:2000].lower(),
        normalize_url(url) or "",
    ]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()


def to_utc(value: datetime | None) -> datetime | None:
    """Coerce a parsed timestamp to timezone-aware UTC.

    Feeds are inconsistent about timezones; a naive datetime is assumed to be UTC rather
    than dropped, because a slightly wrong timestamp is more useful than no article.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def slugify(text: str, max_length: int = 80) -> str:
    """URL-safe slug for sources, tags, feeds, and stories."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_WORD.sub(" ", text.lower())
    text = _WHITESPACE.sub("-", text).strip("-")
    return text[:max_length].strip("-") or "untitled"


@dataclass(frozen=True)
class NormalizedItem:
    """A feed item reduced to the fields the article model needs.

    Original values are kept verbatim alongside the derived ones — the derived fields
    are Layer 2 and may be recomputed when these rules improve, while the originals are
    Layer 1 and never change (§4.2).
    """

    guid: str | None
    url: str
    canonical_url: str | None
    headline: str
    description: str | None
    author: str | None
    image_url: str | None
    published_at: datetime | None
    payload: dict | None

    # Derived (Layer 2)
    normalized_url: str | None
    normalized_title: str | None
    content_hash: str

    @property
    def is_usable(self) -> bool:
        """An item with no URL or no headline cannot be attributed, so we drop it.

        Non-Negotiable #2 requires every displayed fact to trace back to a working link.
        An item that cannot satisfy that has no place in the database.
        """
        return bool(self.url and self.headline)
