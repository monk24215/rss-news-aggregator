"""Users, permissions, saved views, reader preferences, settings — §38, §35.7, §34.7.

Section 3 builds authentication on top of this; Section 2 only establishes the shape,
because half the operational tables need a `user_id` to point at.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import LAYER_OPERATIONS, Base, TimestampMixin
from app.models.enums import USER_ROLES, check_in


class User(Base, TimestampMixin):
    """An administrative user (§38).

    No password column: Section 3 decides the authentication method, and inventing a
    credential shape now would prejudge it. Identity and authorization are here; proof
    of identity is not.
    """

    __tablename__ = "users"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="read_only", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint(check_in("role", USER_ROLES), name="user_role_known"),)


class SavedView(Base, TimestampMixin):
    """A stored administrative filter (§35.7).

    `owner_user_id` NULL means the view is shared with everyone — which is how the
    built-in ones ("Failed Sources", "Stories With Conflicts") ship.
    """

    __tablename__ = "saved_views"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    #: Which admin screen the filter applies to: "sources", "stories", "articles", …
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSONB)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ReaderPreference(Base, TimestampMixin):
    """A reader's personalization (§34.7).

    Deliberately its own table keyed by an opaque `reader_key` rather than columns on a
    story or article: §34.7 requires preferences never to alter canonical records, and
    readers are not administrative users.
    """

    __tablename__ = "reader_preferences"
    __layer__ = LAYER_OPERATIONS

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Anonymous browser/session identifier, or a future reader account id.
    reader_key: Mapped[str] = mapped_column(String(120), nullable=False)
    #: "favorite_topic", "hidden_topic", "favorite_source", "reading_view", "theme", …
    preference: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("reader_key", "preference", "value", name="reader_pref_once"),
    )


class Setting(Base, TimestampMixin):
    """System configuration held in the database (§6).

    Secrets do NOT belong here — those come from the environment (Non-Negotiable #10).
    This is for operational policy an administrator changes at runtime: AI daily/monthly
    limits, confidence thresholds, default fetch intervals.
    """

    __tablename__ = "settings"
    __layer__ = LAYER_OPERATIONS

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


__all__ = ["User", "SavedView", "ReaderPreference", "Setting"]
