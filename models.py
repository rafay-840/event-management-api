"""SQLAlchemy ORM models (the database tables).

Each class maps to one PostgreSQL table. The ``Mapped[...]`` annotations are
the SQLAlchemy 2.0 typed style: the Python type says whether a column is
nullable (``str | None``) or not (``str``).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns filled in by PostgreSQL."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Event(TimestampMixin, Base):
    """A company event such as a workshop, a product launch or a conference."""

    __tablename__ = "events"
    # The database enforces the capacity rule too, as a last line of defence
    # behind the Pydantic validation.
    __table_args__ = (
        CheckConstraint("max_capacity > 0", name="ck_events_max_capacity_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Stored with time zone so "2026-11-20 10:00 Europe/Rome" is unambiguous.
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    location: Mapped[str] = mapped_column(String(200))
    max_capacity: Mapped[int] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<Event id={self.id} title={self.title!r}>"
