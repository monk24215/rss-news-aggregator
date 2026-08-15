"""Feed instances — §29, §29.2, §30.

A feed instance is a saved query plus a presentation policy. The same article/story
database can therefore power completely different publications (§29.2) without any
output channel keeping its own copy of the data (Non-Negotiable #9).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import LAYER_PRESENTATION, Base, TimestampMixin
from app.models.enums import FEED_SORT_ORDERS, RULE_MODES, SUMMARY_LENGTHS, check_in


class FeedInstance(Base, TimestampMixin):
    """A customized feed (§29)."""

    __tablename__ = "feed_instances"
    __layer__ = LAYER_PRESENTATION

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- identity -------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- composition (§29) ----------------------------------------------------
    #: Source *types* included, by id; tags and sources use the join tables below.
    max_articles: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    sort_order: Mapped[str] = mapped_column(String(20), default="newest", nullable=False)
    min_importance_score: Mapped[float | None] = mapped_column(Float)
    min_trending_score: Mapped[float | None] = mapped_column(Float)
    #: §29.2 "maximum article frequency" — a cap per source per hour, so one prolific
    #: publisher cannot crowd out a feed.
    max_articles_per_source_per_hour: Mapped[int | None] = mapped_column(Integer)

    # --- per-feed AI settings (§29.2) ----------------------------------------
    ai_headlines_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_summaries_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clustering_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    synthesis_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trending_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    summary_length: Mapped[str] = mapped_column(
        String(20), default="standard", nullable=False
    )
    headline_style: Mapped[str | None] = mapped_column(String(60))

    # --- output (§30) ---------------------------------------------------------
    rss_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rss_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- health snapshot (§29.3) ---------------------------------------------
    story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stories_last_24h: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tags: Mapped[list[FeedInstanceTag]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )
    sources: Mapped[list[FeedInstanceSource]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )
    source_types: Mapped[list[FeedInstanceSourceType]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(check_in("sort_order", FEED_SORT_ORDERS), name="feed_sort_known"),
        CheckConstraint(
            check_in("summary_length", SUMMARY_LENGTHS), name="feed_summary_length_known"
        ),
        CheckConstraint("max_articles > 0", name="feed_max_articles_positive"),
    )


class FeedInstanceTag(Base):
    """A tag rule on a feed — included, excluded, or required (§29, §29.2).

    `is_required` expresses §29.2's "required tags": an included tag widens the feed,
    a required tag narrows it (every story must carry it).
    """

    __tablename__ = "feed_instance_tags"
    __layer__ = LAYER_PRESENTATION

    feed_instance_id: Mapped[int] = mapped_column(
        ForeignKey("feed_instances.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    feed: Mapped[FeedInstance] = relationship(back_populates="tags")
    tag: Mapped["object"] = relationship("Tag")

    __table_args__ = (
        CheckConstraint(check_in("mode", RULE_MODES), name="feed_tag_mode_known"),
        CheckConstraint(
            "NOT (is_required AND mode = 'exclude')", name="excluded_tag_cannot_be_required"
        ),
        Index("ix_feed_instance_tags_mode", "feed_instance_id", "mode"),
    )


class FeedInstanceSource(Base):
    """A source rule on a feed (§29)."""

    __tablename__ = "feed_instance_sources"
    __layer__ = LAYER_PRESENTATION

    feed_instance_id: Mapped[int] = mapped_column(
        ForeignKey("feed_instances.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(10), nullable=False)

    feed: Mapped[FeedInstance] = relationship(back_populates="sources")
    source: Mapped["object"] = relationship("Source")

    __table_args__ = (
        CheckConstraint(check_in("mode", RULE_MODES), name="feed_source_mode_known"),
    )


class FeedInstanceSourceType(Base):
    """A source-type rule on a feed (§29 lists source types as a composition input)."""

    __tablename__ = "feed_instance_source_types"
    __layer__ = LAYER_PRESENTATION

    feed_instance_id: Mapped[int] = mapped_column(
        ForeignKey("feed_instances.id", ondelete="CASCADE"), primary_key=True
    )
    source_type_id: Mapped[int] = mapped_column(
        ForeignKey("source_types.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(10), nullable=False)

    feed: Mapped[FeedInstance] = relationship(back_populates="source_types")

    __table_args__ = (
        CheckConstraint(check_in("mode", RULE_MODES), name="feed_source_type_mode_known"),
    )


class FeedInstanceEntry(Base):
    """A story's membership in a feed, with the reason it qualified (§35.5).

    Materializing membership is what lets the public page render without running the
    rule engine on the request path (Non-Negotiable #7), and `match_reason` is what the
    "why am I seeing this?" panel reads.
    """

    __tablename__ = "feed_instance_entries"
    __layer__ = LAYER_PRESENTATION

    feed_instance_id: Mapped[int] = mapped_column(
        ForeignKey("feed_instances.id", ondelete="CASCADE"), primary_key=True
    )
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rank_score: Mapped[float | None] = mapped_column(Float)
    #: Which rules matched, e.g. {"tags": ["power-grid"], "importance": 82}.
    match_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_feed_entries_order", "feed_instance_id", "published_at"),
        Index("ix_feed_entries_rank", "feed_instance_id", "rank_score"),
    )


__all__ = [
    "FeedInstance",
    "FeedInstanceTag",
    "FeedInstanceSource",
    "FeedInstanceSourceType",
    "FeedInstanceEntry",
]
