"""Sources and tags — §7, §7.1, §7.2, §8.

A source is a publication we fetch from. Everything we know about *how it behaves* as a
feed (health, timing, failures) lives here too, because §35.3 wants that visible per
source rather than buried in logs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import LAYER_ANALYSIS, LAYER_ORIGINAL, Base, TimestampMixin
from app.models.enums import RULE_MODES, SOURCE_CONTEXTS, check_in


class SourceType(Base, TimestampMixin):
    """Configurable source types (§7.1).

    A table, not an enum: the spec says "source types should be configurable", and an
    administrator changing a taxonomy should not require a migration.
    """

    __tablename__ = "source_types"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sources: Mapped[list[Source]] = relationship(back_populates="source_type")


class Tag(Base, TimestampMixin):
    """A tag, reusable across sources, articles, stories, and feeds (§8).

    Hierarchical via `parent_id` (Energy → Power Grid). `is_ai_suggested` records that a
    tag arrived from the model rather than a human, so §8's "administrators may approve,
    remove, or change" has something to act on.
    """

    __tablename__ = "tags"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_ai_suggested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parent: Mapped[Tag | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Tag]] = relationship(back_populates="parent")

    __table_args__ = (
        CheckConstraint("id <> parent_id", name="tag_not_its_own_parent"),
    )


class Source(Base, TimestampMixin):
    """A publication we collect from (§7)."""

    __tablename__ = "sources"
    __layer__ = LAYER_ORIGINAL

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- identity -------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    source_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_types.id", ondelete="SET NULL"), index=True
    )

    #: §7.2 — descriptive context, never a truth score.
    context: Mapped[str | None] = mapped_column(String(40))

    #: Editorial weight used by scoring (§25), not a claim about accuracy.
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- fetch policy (§12) ---------------------------------------------------
    fetch_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=900, nullable=False
    )
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    max_items_per_fetch: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    #: Conditional-request caches so we stop re-downloading unchanged feeds.
    http_etag: Mapped[str | None] = mapped_column(String(400))
    http_last_modified: Mapped[str | None] = mapped_column(String(200))

    # --- health (§35.3) -------------------------------------------------------
    last_fetch_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fetch_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fetch_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_response_ms: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set when §12's "temporary source disablement after repeated failures" trips.
    disabled_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    article_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    source_type: Mapped[SourceType | None] = relationship(back_populates="sources")
    tags: Mapped[list[SourceTag]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            f"context IS NULL OR {check_in('context', SOURCE_CONTEXTS)}",
            name="source_context_known",
        ),
        CheckConstraint("fetch_interval_seconds >= 60", name="fetch_interval_polite"),
        CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
        Index("ix_sources_due", "is_active", "last_fetch_attempted_at"),
    )


class SourceTag(Base):
    """sources ↔ tags (§6)."""

    __tablename__ = "source_tags"
    __layer__ = LAYER_ANALYSIS

    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    source: Mapped[Source] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship()


class SourceFetchLog(Base):
    """One row per fetch attempt (§35.3, §45).

    Kept separate from `sources` so the current health summary stays cheap to read while
    the history stays complete.
    """

    __tablename__ = "source_fetch_logs"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_ms: Mapped[int | None] = mapped_column(Integer)
    items_returned: Mapped[int | None] = mapped_column(Integer)
    items_new: Mapped[int | None] = mapped_column(Integer)
    parse_error: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    #: True when the server answered 304 and we skipped the body (§12).
    not_modified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (Index("ix_source_fetch_logs_recent", "source_id", "attempted_at"),)


# Re-exported for the feed rule joins, which use the same include/exclude vocabulary.
__all__ = [
    "SourceType",
    "Tag",
    "Source",
    "SourceTag",
    "SourceFetchLog",
    "RULE_MODES",
    "UniqueConstraint",
]
