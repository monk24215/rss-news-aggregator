"""Duplicate detection — §13.

§13 lists seven signals and asks that the same article not appear twice. The signals are
checked in order of how much they prove:

  1. `(source_id, guid)`      the publisher's own identifier — decisive within a source
  2. `(source_id, normalized_url)`  same document, campaign parameters stripped
  3. `content_hash`           byte-equal substance, e.g. a wire item syndicated verbatim
  4. `(normalized_title, day)` same headline from the same source on the same day

Signals 1 and 2 are also unique constraints in the schema, so the database is the
backstop if this function is ever bypassed. Checking here first turns what would be an
IntegrityError into an ordinary "already have it" decision.

Note what is deliberately NOT here: two different outlets covering one event are not
duplicates. That is a *story*, and merging them would destroy the source count that
makes the product worth using (§20). Cross-source matching happens in clustering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Article
from app.services.normalize import NormalizedItem


@dataclass(frozen=True)
class DuplicateVerdict:
    """Whether an incoming item is already in the database, and on what evidence."""

    is_duplicate: bool
    existing_article_id: int | None = None
    signal: str | None = None

    @classmethod
    def new(cls) -> DuplicateVerdict:
        return cls(False)

    @classmethod
    def duplicate(cls, article_id: int, signal: str) -> DuplicateVerdict:
        return cls(True, article_id, signal)


def find_duplicate(session: Session, source_id: int, item: NormalizedItem) -> DuplicateVerdict:
    """Return a verdict on whether `item` already exists."""
    # 1. The publisher's own identifier.
    if item.guid:
        found = session.execute(
            select(Article.id).where(
                Article.source_id == source_id, Article.original_guid == item.guid
            )
        ).scalar_one_or_none()
        if found:
            return DuplicateVerdict.duplicate(found, "guid")

    # 2. Same document once campaign parameters are stripped.
    if item.normalized_url:
        found = session.execute(
            select(Article.id).where(
                Article.source_id == source_id,
                Article.normalized_url == item.normalized_url,
            )
        ).scalar_one_or_none()
        if found:
            return DuplicateVerdict.duplicate(found, "normalized_url")

    # 3. Identical substance from anywhere — syndicated wire copy republished verbatim.
    found = session.execute(
        select(Article.id).where(Article.content_hash == item.content_hash).limit(1)
    ).scalar_one_or_none()
    if found:
        return DuplicateVerdict.duplicate(found, "content_hash")

    # 4. Same source, same normalized headline, same day. Catches a publisher reposting
    #    under a fresh GUID and URL — common when a story is "updated" and re-syndicated.
    if item.normalized_title and item.published_at:
        window_start = item.published_at - timedelta(hours=36)
        window_end = item.published_at + timedelta(hours=36)
        found = session.execute(
            select(Article.id)
            .where(
                Article.source_id == source_id,
                Article.normalized_title == item.normalized_title,
                or_(
                    and_(
                        Article.original_published_at.is_not(None),
                        Article.original_published_at >= window_start,
                        Article.original_published_at <= window_end,
                    ),
                    Article.original_published_at.is_(None),
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
        if found:
            return DuplicateVerdict.duplicate(found, "title_and_date")

    return DuplicateVerdict.new()


def count_articles(session: Session, source_id: int) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Article).where(Article.source_id == source_id)
        ).scalar_one()
    )
