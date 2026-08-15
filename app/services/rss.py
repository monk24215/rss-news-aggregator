"""RSS output — §30.

The platform consumes RSS and produces it. A generated item carries our headline and
summary but points at the **original article URL**, and names the original publication
in `dc:creator` and `source` — because Non-Negotiable #2 does not stop at the edge of
our own site. Someone reading this feed in their own reader must still be one click from
the reporting.

Built with `xml.etree` rather than string formatting so that a headline containing an
ampersand cannot produce a broken feed — the exact failure we handle gracefully when
other people's feeds do it to us.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from app.models import FeedInstance
from app.services import presentation
from app.services.feeds import feed_stories

DC_NS = "http://purl.org/dc/elements/1.1/"
ATOM_NS = "http://www.w3.org/2005/Atom"


def _text(parent: ET.Element, tag: str, value: str | None, **attrs: str) -> ET.Element | None:
    if value is None:
        return None
    element = ET.SubElement(parent, tag, attrs)
    element.text = value
    return element


def render_feed(
    session: Session,
    feed: FeedInstance,
    base_url: str,
    limit: int | None = None,
    now: datetime | None = None,
) -> str:
    """Render one feed instance as an RSS 2.0 document (§30)."""
    now = now or datetime.now(timezone.utc)
    base_url = base_url.rstrip("/")

    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    _text(channel, "title", feed.name)
    _text(channel, "link", f"{base_url}/feed/{feed.slug}")
    _text(channel, "description", feed.description or f"{feed.name} — aggregated coverage")
    _text(channel, "language", "en")
    _text(channel, "lastBuildDate", format_datetime(now))
    _text(channel, "generator", "AI RSS News Aggregator")
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {"href": f"{base_url}/rss/{feed.slug}", "rel": "self", "type": "application/rss+xml"},
    )
    if feed.image_url:
        image = ET.SubElement(channel, "image")
        _text(image, "url", feed.image_url)
        _text(image, "title", feed.name)
        _text(image, "link", f"{base_url}/feed/{feed.slug}")

    for story in feed_stories(session, feed, limit=limit):
        card = presentation.build_card(session, story)
        if not card.articles:
            continue
        primary = card.articles[0]

        item = ET.SubElement(channel, "item")
        _text(item, "title", card.headline.text)

        # The link points at the original article, not at us. §30 lists both, and the
        # publisher's URL is the one that matters to a reader.
        _text(item, "link", primary.original_url)
        _text(item, "guid", f"{base_url}/story/{story.slug or story.id}", isPermaLink="false")
        _text(item, "comments", f"{base_url}/story/{story.slug or story.id}")

        description_parts: list[str] = []
        if card.summary:
            label = "AI summary" if card.summary.is_ai else "From the publisher"
            description_parts.append(f"{label}: {card.summary.text}")
        if card.sources:
            names = " · ".join(s.name for s in card.sources)
            description_parts.append(f"Reported by: {names}")
        description_parts.append(f"Original: {primary.original_url}")
        _text(item, "description", "\n\n".join(description_parts))

        published = story.first_reported_at or story.last_updated_at or now
        _text(item, "pubDate", format_datetime(published))

        source_name = card.sources[0].name if card.sources else None
        if source_name:
            _text(item, f"{{{DC_NS}}}creator", source_name)
            source_el = _text(item, "source", source_name)
            if source_el is not None and card.sources[0].website_url:
                source_el.set("url", card.sources[0].website_url)

        for tag in card.tags:
            _text(item, "category", tag.name)

        if card.image_url:
            ET.SubElement(
                item, "enclosure", {"url": card.image_url, "type": "image/jpeg", "length": "0"}
            )

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode")
