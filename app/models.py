"""ORM models.

Section 1 defines ONLY the heartbeat table — the minimal table the scheduler→worker
proof writes to. Domain models (sources, articles, stories, tags) are Section 2 and will
be built against the three-layer data model (§4.2, §6). Do not add domain tables here.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # A short label lets us confirm which process wrote the tick.
    source: Mapped[str] = mapped_column(String(50), default="scheduler", nullable=False)
