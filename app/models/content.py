"""Articles and stories — the heart of the schema.

Articles are the raw material; stories are the primary information unit (§2). The two
rules that shape every column here:

  - Original publisher data is written once and never updated (Non-Negotiable #1). Those
    columns are prefixed `original_` and a database trigger rejects any UPDATE that
    changes them — see migration 0002. Convention alone is not enough for a rule the
    whole product's credibility rests on.
  - We never store the article body (Non-Negotiable #6). There is deliberately no
    `content` column. `original_description` holds only the feed's own summary field.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    LAYER_ANALYSIS,
    LAYER_ORIGINAL,
    LAYER_PRESENTATION,
    Base,
    TimestampMixin,
)
from app.models.enums import ENTITY_KINDS, STORY_STATES, check_in


class Article(Base, TimestampMixin):
    """One item retrieved from one source (§9).

    Carries Layer 1 (original_*) and Layer 2 (analysis) columns in one row, which is
    what §4.2 describes. Layer 3 lives in `ai_results`, versioned, so a regenerated
    headline never overwrites the previous one.
    """

    __tablename__ = "articles"
    __layer__ = LAYER_ORIGINAL

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- LAYER 1: original publisher data — immutable after insert ------------
    original_guid: Mapped[str | None] = mapped_column(String(500))
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_canonical_url: Mapped[str | None] = mapped_column(Text)
    original_headline: Mapped[str] = mapped_column(Text, nullable=False)
    original_description: Mapped[str | None] = mapped_column(Text)
    original_author: Mapped[str | None] = mapped_column(String(300))
    original_image_url: Mapped[str | None] = mapped_column(Text)
    original_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Anything else the feed carried, kept verbatim for later re-processing.
    original_payload: Mapped[dict | None] = mapped_column(JSONB)

    # --- LAYER 2: system analysis ---------------------------------------------
    #: Dedup signals (§13). Normalized forms are derived, so they are analysis, not
    #: original — they may be recomputed when the normalizer improves.
    normalized_url: Mapped[str | None] = mapped_column(Text, index=True)
    normalized_title: Mapped[str | None] = mapped_column(Text, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), index=True
    )
    #: Set when this article was found to duplicate another; the survivor is `duplicate_of`.
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    importance_score: Mapped[float | None] = mapped_column(Float)
    #: Confidence that this article belongs to its assigned story (§27.1).
    story_match_confidence: Mapped[float | None] = mapped_column(Float)

    # --- editorial (§35.4) ----------------------------------------------------
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_processing_disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    #: True once a human edits anything on this article; automation must not undo it.
    has_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    source: Mapped["object"] = relationship("Source", viewonly=True)
    story: Mapped[Story | None] = relationship(back_populates="articles", foreign_keys=[story_id])
    tags: Mapped[list[ArticleTag]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    entities: Mapped[list[ArticleEntity]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # §13: the same item from the same source arrives once.
        UniqueConstraint("source_id", "original_guid", name="source_guid"),
        UniqueConstraint("source_id", "normalized_url", name="source_normalized_url"),
        CheckConstraint(
            "importance_score IS NULL OR (importance_score >= 0 AND importance_score <= 100)",
            name="importance_in_range",
        ),
        CheckConstraint(
            "story_match_confidence IS NULL OR "
            "(story_match_confidence >= 0 AND story_match_confidence <= 1)",
            name="confidence_in_unit_range",
        ),
        CheckConstraint("id <> duplicate_of_id", name="article_not_its_own_duplicate"),
        Index("ix_articles_published", "original_published_at"),
        Index("ix_articles_story_published", "story_id", "original_published_at"),
    )


class Story(Base, TimestampMixin):
    """An underlying event covered by one or more articles (§10, §21)."""

    __tablename__ = "stories"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str | None] = mapped_column(String(300), unique=True)

    #: §21 lifecycle. Automatic where practical, always manually overrideable.
    state: Mapped[str] = mapped_column(String(20), default="emerging", nullable=False)
    state_is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    first_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Distinct sources, not article count — §25 counts independent voices.
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    article_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    importance_score: Mapped[float | None] = mapped_column(Float)
    trending_score: Mapped[float | None] = mapped_column(Float)

    # --- editorial flags (§35.4) ----------------------------------------------
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: §19 — locked stories are never re-clustered automatically.
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_unresolved_conflicts: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    articles: Mapped[list[Article]] = relationship(
        back_populates="story", foreign_keys=[Article.story_id]
    )
    memberships: Mapped[list[StoryArticle]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )
    tags: Mapped[list[StoryTag]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )
    timeline: Mapped[list[StoryTimelineEvent]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )
    claims: Mapped[list[StoryClaim]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(check_in("state", STORY_STATES), name="story_state_known"),
        CheckConstraint("source_count >= 0", name="source_count_non_negative"),
        CheckConstraint(
            "trending_score IS NULL OR (trending_score >= 0 AND trending_score <= 100)",
            name="trending_in_range",
        ),
        Index("ix_stories_trending", "trending_score", "last_updated_at"),
        Index("ix_stories_state", "state", "last_updated_at"),
    )


class StoryArticle(Base):
    """stories ↔ articles (§6), with the provenance of the assignment.

    An article's current story also lives denormalized on `articles.story_id` for cheap
    reads; this table is the record of *how* it got there, including who moved it.
    """

    __tablename__ = "story_articles"
    __layer__ = LAYER_ANALYSIS

    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    #: The article that opened the story — powers "First reported by…" (§34.3).
    is_first_report: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    story: Mapped[Story] = relationship(back_populates="memberships")
    article: Mapped[Article] = relationship()


class ArticleTag(Base):
    """articles ↔ tags, carrying the confidence that justified the tag (§27.1)."""

    __tablename__ = "article_tags"
    __layer__ = LAYER_ANALYSIS

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    article: Mapped[Article] = relationship(back_populates="tags")
    tag: Mapped["object"] = relationship("Tag")

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="article_tag_confidence_range",
        ),
    )


class StoryTag(Base):
    """stories ↔ tags (§10 lists tags on the story record)."""

    __tablename__ = "story_tags"
    __layer__ = LAYER_ANALYSIS

    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    story: Mapped[Story] = relationship(back_populates="tags")
    tag: Mapped["object"] = relationship("Tag")


class ArticleEntity(Base):
    """Named entities extracted from an article (§19 clustering signals)."""

    __tablename__ = "article_entities"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Case/punctuation-folded form used for matching between articles.
    normalized_value: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float)

    article: Mapped[Article] = relationship(back_populates="entities")

    __table_args__ = (
        CheckConstraint(check_in("kind", ENTITY_KINDS), name="entity_kind_known"),
        UniqueConstraint("article_id", "kind", "normalized_value", name="entity_once"),
    )


class StoryTimelineEvent(Base, TimestampMixin):
    """A dated development within a story (§22).

    `source_article_id` is not optional in spirit: §22 requires every timeline entry to
    identify the reporting that supports it. It is nullable only so an administrator can
    add an editorial note, which is why `is_manual` exists to tell the two apart.
    """

    __tablename__ = "story_timeline_events"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL")
    )
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    story: Mapped[Story] = relationship(back_populates="timeline")

    __table_args__ = (
        CheckConstraint(
            "source_article_id IS NOT NULL OR is_manual", name="timeline_entry_is_sourced"
        ),
        Index("ix_story_timeline_order", "story_id", "occurred_at"),
    )


class StoryClaim(Base, TimestampMixin):
    """A contested fact within a story (§24).

    One row per claim *subject* ("how many customers were affected"); the competing
    values hang off it in `story_claim_values`. Conflicts are tracked until an
    authoritative source resolves them — never silently reconciled.
    """

    __tablename__ = "story_claims"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    is_conflicting: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Which value won, and on whose authority — set only when genuinely resolved.
    #: `use_alter` because claims and values reference each other; the constraint is
    #: added after both tables exist rather than creating an unorderable cycle.
    resolved_value_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "story_claim_values.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_story_claims_resolved_value_id_story_claim_values",
        )
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)

    story: Mapped[Story] = relationship(back_populates="claims")
    values: Mapped[list[StoryClaimValue]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        foreign_keys="StoryClaimValue.claim_id",
    )


class StoryClaimValue(Base, TimestampMixin):
    """One source's version of a contested fact (§24)."""

    __tablename__ = "story_claim_values"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("story_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)

    claim: Mapped[StoryClaim] = relationship(back_populates="values", foreign_keys=[claim_id])


class ScoringFactor(Base):
    """The inputs behind a score, stored individually (§23, §25, §35.5).

    §25 is explicit: "store the individual scoring factors so administrators can
    understand why a story is trending". A single number cannot be explained after the
    fact, so we keep the parts.
    """

    __tablename__ = "scoring_factors"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    #: "trending" or "importance".
    score_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    factor: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    #: Human-readable line for the "why am I seeing this?" panel.
    explanation: Mapped[str | None] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "story_id IS NOT NULL OR article_id IS NOT NULL",
            name="scoring_factor_has_target",
        ),
        Index("ix_scoring_factors_lookup", "story_id", "score_kind", "computed_at"),
    )


__all__ = [
    "Article",
    "Story",
    "StoryArticle",
    "ArticleTag",
    "StoryTag",
    "ArticleEntity",
    "StoryTimelineEvent",
    "StoryClaim",
    "StoryClaimValue",
    "ScoringFactor",
    "LAYER_PRESENTATION",
]
