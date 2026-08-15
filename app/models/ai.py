"""AI results, review queue, and cost — §16, §26, §27, §28.

Layer 3 lives here. Nothing in this module is ever written into an article's original
columns; a regenerated headline creates a NEW row and moves the `is_current` flag, so
§26's versioning requirement holds by construction rather than by discipline.
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
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import LAYER_ANALYSIS, LAYER_PRESENTATION, Base, TimestampMixin
from app.models.enums import (
    AI_OPERATIONS,
    AI_TARGETS,
    REVIEW_STATES,
    check_in,
)


class AIResult(Base, TimestampMixin):
    """One generated artifact, versioned (§26).

    Every field §26 asks to store is a column here: content, provider, model, prompt
    version, timestamp, the input version it was generated from, human modifications,
    and which version is current.
    """

    __tablename__ = "ai_results"
    __layer__ = LAYER_PRESENTATION

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- what this is attached to --------------------------------------------
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(40), nullable=False)

    # --- the generated artifact ----------------------------------------------
    content: Mapped[str | None] = mapped_column(Text)
    #: Structured output (entities, cluster decisions, conflict lists) when the
    #: operation does not return prose.
    payload: Mapped[dict | None] = mapped_column(JSONB)

    # --- provenance (§26) -----------------------------------------------------
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(60))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Hash of the inputs, so we can tell whether regeneration is even needed (§28).
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- human involvement ----------------------------------------------------
    #: An editor's replacement text. The generated `content` above stays untouched.
    edited_content: Mapped[str | None] = mapped_column(Text)
    edited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- confidence and review (§27) -----------------------------------------
    confidence: Mapped[float | None] = mapped_column(Float)
    review_state: Mapped[str] = mapped_column(
        String(20), default="auto_accepted", nullable=False
    )

    #: The exact articles this artifact was generated from (§15: "store the source
    #: articles used to generate every synthesized story").
    inputs: Mapped[list[AIResultInput]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )

    @property
    def display_content(self) -> str | None:
        """What the reader sees: a human edit wins over the generated text (§35.4)."""
        return self.edited_content or self.content

    __table_args__ = (
        CheckConstraint(check_in("target_type", AI_TARGETS), name="ai_target_known"),
        CheckConstraint(check_in("operation", AI_OPERATIONS), name="ai_operation_known"),
        CheckConstraint(check_in("review_state", REVIEW_STATES), name="ai_review_state_known"),
        CheckConstraint(
            "(target_type = 'article' AND article_id IS NOT NULL AND story_id IS NULL) OR "
            "(target_type = 'story' AND story_id IS NOT NULL AND article_id IS NULL)",
            name="ai_target_matches_id",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ai_confidence_range",
        ),
        Index("ix_ai_results_history", "target_type", "operation", "generated_at"),
    )


# Exactly one *current* version per (target, operation) — partial unique indexes, so
# history rows are unconstrained while the live pointer can never fork.
#
# Two indexes rather than one over (article_id, story_id): in Postgres NULLs are
# distinct in a unique index, so a single index covering both columns would never fire
# for article-targeted rows (their story_id is NULL, making every row "unique"). Each
# index is therefore restricted to the target it actually applies to.
_ai = AIResult.__table__
Index(
    "uq_ai_results_current_article",
    _ai.c.article_id,
    _ai.c.operation,
    unique=True,
    postgresql_where=_ai.c.is_current.is_(True) & _ai.c.article_id.isnot(None),
)
Index(
    "uq_ai_results_current_story",
    _ai.c.story_id,
    _ai.c.operation,
    unique=True,
    postgresql_where=_ai.c.is_current.is_(True) & _ai.c.story_id.isnot(None),
)


class AIResultInput(Base):
    """Which article fed which generated artifact (§15, §20).

    This is what makes "generated from 6 source reports" (§34.5) a fact we can render
    rather than a number we invent.
    """

    __tablename__ = "ai_result_inputs"
    __layer__ = LAYER_PRESENTATION

    ai_result_id: Mapped[int] = mapped_column(
        ForeignKey("ai_results.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )

    result: Mapped[AIResult] = relationship(back_populates="inputs")


class ReviewQueueItem(Base, TimestampMixin):
    """A decision routed to a human (§27.2).

    Low-confidence tags, questionable merges and splits, conflicts, and unusual
    synthesis all land in one queue so an administrator resolves them from one screen
    instead of opening each article.
    """

    __tablename__ = "review_queue_items"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: e.g. "story_merge", "story_split", "low_confidence_tag", "conflict", "synthesis".
    reason: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    ai_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_results.id", ondelete="CASCADE")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    state: Mapped[str] = mapped_column(String(20), default="pending_review", nullable=False)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(check_in("state", REVIEW_STATES), name="review_item_state_known"),
        CheckConstraint(
            "article_id IS NOT NULL OR story_id IS NOT NULL OR ai_result_id IS NOT NULL",
            name="review_item_has_target",
        ),
        Index("ix_review_queue_open", "state", "created_at"),
    )


class AIUsage(Base):
    """Per-call cost and token accounting (§28).

    Attributing usage to a source, feed, and story is what makes "cost by source / by
    feed / by story" answerable without re-deriving it later.
    """

    __tablename__ = "ai_usage"
    __layer__ = LAYER_ANALYSIS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    #: Money, so exact decimal rather than float.
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    # Attribution (all optional — a classification call may belong to no feed).
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), index=True
    )
    feed_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("feed_instances.id", ondelete="SET NULL"), index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        CheckConstraint(check_in("operation", AI_OPERATIONS), name="usage_operation_known"),
        Index("ix_ai_usage_rollup", "occurred_at", "provider", "model"),
    )


__all__ = [
    "AIResult",
    "AIResultInput",
    "ReviewQueueItem",
    "AIUsage",
]
