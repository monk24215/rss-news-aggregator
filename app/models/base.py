"""Declarative base and shared column conventions.

Every table in this schema belongs to exactly one of the three layers in §4.2:

  LAYER 1 — ORIGINAL     what the publisher said. Written once at ingestion, never
                         updated. Columns are prefixed `original_` and protected by a
                         database trigger (see migration 0002), not merely by convention.
  LAYER 2 — ANALYSIS     what the system concluded: tags, story assignment, scores,
                         entities, duplicate status, confidence, conflicts.
  LAYER 3 — PRESENTATION what we show: AI headlines, AI summaries, story synthesis.

Each model declares its layer in `__layer__` so the separation is visible in the code
and assertable in tests. Mixing layers inside one row is allowed only where the spec
already does it (an article row carries original columns and analysis columns), and in
that case the trigger is what keeps Layer 1 honest.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names make migrations reviewable and autogenerate stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

LAYER_ORIGINAL = "original"
LAYER_ANALYSIS = "analysis"
LAYER_PRESENTATION = "presentation"
LAYER_OPERATIONS = "operations"

LAYERS = frozenset(
    {LAYER_ORIGINAL, LAYER_ANALYSIS, LAYER_PRESENTATION, LAYER_OPERATIONS}
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    #: Which of the three data layers (§4.2) this table belongs to.
    __layer__: str = LAYER_OPERATIONS


class TimestampMixin:
    """created_at / updated_at on every domain table.

    `updated_at` is maintained by the database (see the `set_updated_at` trigger in
    migration 0002) so a row cannot be quietly modified without the timestamp moving —
    which is what makes the audit trail in §36/§37 trustworthy.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
