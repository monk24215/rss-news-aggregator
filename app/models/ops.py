"""Operational tables — jobs, logs, audit, change history — §14, §36, §37, §45.

Three different record types that are easy to conflate and must not be:

  system_logs    technical events, for engineers.
  audit_logs     who changed what, in human terms, for accountability (§37).
  change_history previous → new values, so a change can be undone (§36).

§37 is explicit that the audit log is "separate from technical system logs", and undo
needs the old value, which a log line cannot reliably give you.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LAYER_OPERATIONS, Base, TimestampMixin
from app.models.enums import JOB_STAGES, JOB_STATUSES, check_in


class Heartbeat(Base):
    """Section 1's liveness proof: scheduler → Redis → worker → Postgres.

    Kept because it is the cheapest end-to-end assertion that the backbone still works,
    and it costs one narrow row per tick.
    """

    __tablename__ = "heartbeats"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(50), default="scheduler", nullable=False)


class ProcessingJob(Base, TimestampMixin):
    """One pipeline stage for one item (§14).

    Rows are per *stage*, not per article, because Non-Negotiable #8 requires each stage
    to be independently retryable: a failed AI generation must not force a re-fetch.
    """

    __tablename__ = "processing_jobs"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    #: Backoff target — the job is invisible to the worker until this passes (§12).
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    #: The arq job id, so a database row can be traced to a queue entry.
    queue_job_id: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        CheckConstraint(check_in("stage", JOB_STAGES), name="job_stage_known"),
        CheckConstraint(check_in("status", JOB_STATUSES), name="job_status_known"),
        CheckConstraint("attempts >= 0", name="job_attempts_non_negative"),
        Index("ix_processing_jobs_runnable", "status", "run_after"),
        Index("ix_processing_jobs_stage_status", "stage", "status"),
    )


class SystemLog(Base):
    """Technical events (§6, §45)."""

    __tablename__ = "system_logs"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Which process wrote it: "web", "worker", "scheduler".
    component: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_system_logs_recent", "logged_at", "level"),)


class AuditLog(Base):
    """Who changed what, in human terms (§37).

    `summary` is the sentence an administrator reads ("John changed Power Grid News —
    added tag: Infrastructure"). The machine-readable before/after lives in
    `change_history`, linked by `change_id`.
    """

    __tablename__ = "audit_logs"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: "story.merge", "feed.update", "source.disable", …
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    change_id: Mapped[int | None] = mapped_column(
        ForeignKey("change_history.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id", "occurred_at"),)


class ChangeHistory(Base):
    """Previous → new values for a reversible change (§36).

    §36 lists exactly what must be recorded — previous value, new value, user,
    timestamp, reason — and asks for undo "where safe and practical". `undone_at` marks
    a change that has been reversed, so undo is itself auditable.
    """

    __tablename__ = "change_history"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    is_undoable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_change_history_entity", "entity_type", "entity_id", "occurred_at"),
    )


class Notification(Base, TimestampMixin):
    """An operational alert queued for delivery (§39).

    Stored, not fired-and-forgotten, so "a source has failed repeatedly" survives a
    worker restart and so delivery stays independent of the article/story model.
    """

    __tablename__ = "notifications"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: "source_failing", "processing_stopped", "ai_provider_failure", "queue_backlog",
    #: "feed_empty", "large_story", "review_required".
    trigger: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning", nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB)
    #: "email", "slack", "discord", "webhook".
    channel: Mapped[str | None] = mapped_column(String(30))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_notifications_undelivered", "delivered_at", "created_at"),)


__all__ = [
    "Heartbeat",
    "ProcessingJob",
    "SystemLog",
    "AuditLog",
    "ChangeHistory",
    "Notification",
]
