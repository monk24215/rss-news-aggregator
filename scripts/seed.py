"""Seed a usable installation — sources, tags, and a default feed.

Run once against an empty database:

    python -m scripts.seed

Idempotent: existing rows are left alone, so it is safe to re-run after adding entries
here. It creates nothing an administrator could not create by hand; it just means a
fresh deploy has something to show within one fetch interval instead of an empty page.

The sources are public, well-behaved feeds from organizations that publish them for
exactly this purpose. Fetch intervals are deliberately unhurried (§12).
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import FeedInstance, Source, SourceType, Tag

SOURCE_TYPES = [
    ("News", "news", "General news publications"),
    ("Government", "government", "Official government and agency feeds"),
    ("Technology", "technology", "Technology press"),
    ("Business", "business", "Business and markets"),
    ("Science", "science", "Research and science coverage"),
    ("Weather", "weather", "Weather and hazard alerts"),
]

#: Hierarchical starter vocabulary — §8's own example, plus enough breadth that
#: rule-based tagging has something to match on day one.
TAGS = [
    ("Energy", "energy", None),
    ("Power Grid", "power-grid", "energy"),
    ("Utilities", "utilities", "energy"),
    ("Nuclear", "nuclear", "energy"),
    ("Solar", "solar", "energy"),
    ("Oil", "oil", "energy"),
    ("Gas", "gas", "energy"),
    ("Infrastructure", "infrastructure", None),
    ("Technology", "technology", None),
    ("Artificial Intelligence", "artificial-intelligence", "technology"),
    ("Security", "security", "technology"),
    ("Business", "business", None),
    ("Markets", "markets", "business"),
    ("Science", "science", None),
    ("Climate", "climate", "science"),
    ("Space", "space", "science"),
    ("Health", "health", None),
    ("Weather", "weather", None),
    ("Government", "government", None),
    ("Opinion", "opinion", None),
]

#: (name, slug, site, feed, type slug, priority, interval seconds)
SOURCES = [
    ("NASA Breaking News", "nasa", "https://www.nasa.gov",
     "https://www.nasa.gov/news-release/feed/", "government", 8, 3600),
    ("NOAA National Weather Service", "nws", "https://www.weather.gov",
     "https://www.weather.gov/rss_page.php?site_name=nws", "weather", 7, 1800),
    ("US Energy Information Administration", "eia", "https://www.eia.gov",
     "https://www.eia.gov/rss/todayinenergy.xml", "government", 8, 3600),
    ("NIST News", "nist", "https://www.nist.gov",
     "https://www.nist.gov/news-events/news/rss.xml", "government", 7, 3600),
    ("Federal Reserve Press Releases", "federal-reserve", "https://www.federalreserve.gov",
     "https://www.federalreserve.gov/feeds/press_all.xml", "government", 8, 3600),
    ("Ars Technica", "ars-technica", "https://arstechnica.com",
     "https://feeds.arstechnica.com/arstechnica/index", "technology", 6, 1800),
    ("Hacker News Front Page", "hacker-news", "https://news.ycombinator.com",
     "https://hnrss.org/frontpage", "technology", 4, 1800),
    ("Phys.org", "phys-org", "https://phys.org",
     "https://phys.org/rss-feed/", "science", 5, 3600),

    # General news outlets that cover the SAME events. Without several of these the
    # platform has nothing to cluster and looks like an RSS reader — §3's four-reports-
    # one-story claim needs overlapping coverage to be visible at all.
    ("NPR News", "npr", "https://www.npr.org",
     "https://feeds.npr.org/1001/rss.xml", "news", 8, 900),
    ("BBC News", "bbc", "https://www.bbc.co.uk/news",
     "https://feeds.bbci.co.uk/news/world/rss.xml", "news", 8, 900),
    ("The Guardian — World", "guardian", "https://www.theguardian.com",
     "https://www.theguardian.com/world/rss", "news", 7, 900),
    ("Al Jazeera", "al-jazeera", "https://www.aljazeera.com",
     "https://www.aljazeera.com/xml/rss/all.xml", "news", 7, 900),
    ("CBC Top Stories", "cbc", "https://www.cbc.ca",
     "https://www.cbc.ca/webfeed/rss/rss-topstories", "news", 7, 900),
    ("Deutsche Welle", "dw", "https://www.dw.com",
     "https://rss.dw.com/rdf/rss-en-all", "news", 6, 900),
    ("France 24", "france24", "https://www.france24.com",
     "https://www.france24.com/en/rss", "news", 6, 900),
    ("Sky News — World", "sky-news", "https://news.sky.com",
     "https://feeds.skynews.com/feeds/rss/world.xml", "news", 6, 900),
]

FEEDS = [
    ("Top Stories", "top-stories", "Everything the platform is tracking, most active first",
     "trending"),
    ("Power Grid News", "power-grid", "Energy, utilities, and grid infrastructure", "newest"),
]


def seed() -> int:
    session = SessionLocal()
    created = {"types": 0, "tags": 0, "sources": 0, "feeds": 0}
    try:
        type_ids: dict[str, int] = {}
        for name, slug, description in SOURCE_TYPES:
            existing = session.execute(
                select(SourceType).where(SourceType.slug == slug)
            ).scalar_one_or_none()
            if existing is None:
                existing = SourceType(name=name, slug=slug, description=description)
                session.add(existing)
                session.flush()
                created["types"] += 1
            type_ids[slug] = existing.id

        tag_ids: dict[str, int] = {}
        for name, slug, parent_slug in TAGS:
            existing = session.execute(
                select(Tag).where(Tag.slug == slug)
            ).scalar_one_or_none()
            if existing is None:
                existing = Tag(
                    name=name,
                    slug=slug,
                    parent_id=tag_ids.get(parent_slug) if parent_slug else None,
                )
                session.add(existing)
                session.flush()
                created["tags"] += 1
            tag_ids[slug] = existing.id

        for name, slug, site, feed_url, type_slug, priority, interval in SOURCES:
            existing = session.execute(
                select(Source).where(Source.slug == slug)
            ).scalar_one_or_none()
            if existing is not None:
                continue
            session.add(
                Source(
                    name=name,
                    slug=slug,
                    website_url=site,
                    feed_url=feed_url,
                    source_type_id=type_ids.get(type_slug),
                    priority=priority,
                    fetch_interval_seconds=interval,
                    is_active=True,
                )
            )
            created["sources"] += 1

        for name, slug, description, sort_order in FEEDS:
            existing = session.execute(
                select(FeedInstance).where(FeedInstance.slug == slug)
            ).scalar_one_or_none()
            if existing is not None:
                continue
            feed = FeedInstance(
                name=name, slug=slug, description=description, sort_order=sort_order
            )
            session.add(feed)
            session.flush()
            created["feeds"] += 1

            # Power Grid News gets §29's own example rules; Top Stories takes everything.
            if slug == "power-grid":
                from app.models import FeedInstanceTag

                for tag_slug in ("power-grid", "utilities", "infrastructure", "energy"):
                    if tag_slug in tag_ids:
                        session.add(
                            FeedInstanceTag(
                                feed_instance_id=feed.id,
                                tag_id=tag_ids[tag_slug],
                                mode="include",
                            )
                        )
                if "opinion" in tag_ids:
                    session.add(
                        FeedInstanceTag(
                            feed_instance_id=feed.id, tag_id=tag_ids["opinion"], mode="exclude"
                        )
                    )

        session.commit()
        print(
            f"seeded: {created['types']} source types, {created['tags']} tags, "
            f"{created['sources']} sources, {created['feeds']} feeds"
        )
        return 0
    except Exception as exc:
        session.rollback()
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(seed())
