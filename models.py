"""SQLAlchemy ORM models (the database tables).

Each class maps to one PostgreSQL table. The ``Mapped[...]`` annotations are
the SQLAlchemy 2.0 typed style: the Python type says whether a column is
nullable (``str | None``) or not (``str``).
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

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

    # One event has many registrations. ``passive_deletes=True`` lets the
    # ``ON DELETE CASCADE`` foreign key in PostgreSQL remove them, instead of
    # SQLAlchemy loading every registration just to delete it.
    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )

    # Note: ``Event.registration_count`` is attached after the Registration
    # class below, because its SQL expression needs that class to exist.

    def __repr__(self) -> str:
        return f"<Event id={self.id} title={self.title!r}>"


class Registration(Base):
    """An employee's seat at an event."""

    __tablename__ = "registrations"
    # Business rule "no double registration", enforced by the database itself.
    # Even if two identical requests arrive at the same moment, PostgreSQL
    # will accept only one of them.
    __table_args__ = (
        UniqueConstraint("event_id", "employee_email", name="uq_registration_event_email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    employee_name: Mapped[str] = mapped_column(String(120))
    # Stored in lower case (see schemas.py) so "Ana@Acme.com" == "ana@acme.com".
    employee_email: Mapped[str] = mapped_column(String(254))
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    event: Mapped[Event] = relationship(back_populates="registrations")

    def __repr__(self) -> str:
        return f"<Registration id={self.id} event_id={self.event_id} email={self.employee_email!r}>"


# Number of registrations, computed by PostgreSQL in the same SELECT that
# loads the event (a correlated scalar subquery). This avoids the "N+1 query"
# problem when listing many events: one query returns events and their counts.
Event.registration_count = column_property(
    select(func.count(Registration.id))
    .where(Registration.event_id == Event.id)
    .correlate_except(Registration)
    .scalar_subquery()
)
