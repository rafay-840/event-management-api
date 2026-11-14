"""Business logic and database operations.

The functions here know nothing about HTTP. They receive an ``AsyncSession``
plus validated Pydantic data, talk to the database, and either return ORM
objects or raise one of the domain exceptions defined below. ``main.py``
translates those exceptions into HTTP status codes.

Keeping HTTP out of this layer means the same rules can be reused from a CLI,
a background job or a test without going through FastAPI.
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import DUMMY_PASSWORD_HASH, hash_password, verify_password
from db import settings
from models import Event, Registration, User
from schemas import EventCreate, EventUpdate, RegistrationCreate, UserCreate

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ServiceError(Exception):
    """Base class for every expected business-rule failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """The requested resource does not exist (mapped to HTTP 404)."""


class ConflictError(ServiceError):
    """The request clashes with the current state of the data (HTTP 409)."""


class AuthenticationError(ServiceError):
    """Credentials are wrong (mapped to HTTP 401)."""


class InvalidCredentialsError(AuthenticationError):
    """Raised on a failed login. The message never says *which* part was wrong."""

    def __init__(self) -> None:
        super().__init__("Incorrect email or password")


class EmailAlreadyRegisteredError(ConflictError):
    """Raised when signing up with an e-mail that already has an account."""

    def __init__(self, email: str) -> None:
        super().__init__(f"An account with email {email} already exists")


class EventNotFoundError(NotFoundError):
    """Raised when no event has the given id."""

    def __init__(self, event_id: int) -> None:
        super().__init__(f"Event with id {event_id} not found")


class RegistrationNotFoundError(NotFoundError):
    """Raised when no registration has the given id."""

    def __init__(self, registration_id: int) -> None:
        super().__init__(f"Registration with id {registration_id} not found")


class DuplicateRegistrationError(ConflictError):
    """Raised when an employee tries to register twice for the same event."""

    def __init__(self, email: str, event_id: int) -> None:
        super().__init__(f"{email} is already registered for event {event_id}")


class EventFullError(ConflictError):
    """Raised when an event has no seats left."""

    def __init__(self, event_id: int, capacity: int) -> None:
        super().__init__(f"Event {event_id} is full (maximum capacity {capacity})")


class CapacityBelowRegistrationsError(ConflictError):
    """Raised when an update would set capacity below the seats already taken."""

    def __init__(self, new_capacity: int, registered: int) -> None:
        super().__init__(
            f"max_capacity cannot be {new_capacity}: {registered} employees are already registered"
        )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Return the user with this e-mail, or None."""
    return await db.scalar(select(User).where(User.email == email.lower()))


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    """Create an account, storing only a hash of the password."""
    if await get_user_by_email(db, data.email) is not None:
        raise EmailAlreadyRegisteredError(data.email)

    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:  # two sign-ups with the same e-mail at once
        await db.rollback()
        raise EmailAlreadyRegisteredError(data.email) from exc
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Return the user if the credentials are correct, else raise.

    The password is checked even when the e-mail is unknown (against a dummy
    hash) so both failure cases take the same time.
    """
    user = await get_user_by_email(db, email)
    stored_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, stored_hash)
    if user is None or not password_ok:
        raise InvalidCredentialsError()
    return user


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def create_event(db: AsyncSession, data: EventCreate) -> Event:
    """Insert a new event and return it with its generated id."""
    event = Event(**data.model_dump())
    db.add(event)
    await db.commit()
    # ``refresh`` loads values generated by PostgreSQL (id, created_at, ...).
    await db.refresh(event)
    return event


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """Return the [start, end) instants of a calendar day in the given time zone.

    Comparing ``start <= event_date < end`` (a half-open range) is both
    correct at midnight and able to use the index on ``event_date``, unlike
    casting every row with ``DATE(event_date) = ...``.
    """
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


async def list_events(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 10,
    title: str | None = None,
    on_date: date | None = None,
) -> tuple[Sequence[Event], int]:
    """Return one page of events (soonest first) and the total number of matches.

    Args:
        skip: how many matching events to skip (offset).
        limit: maximum number of events to return.
        title: case-insensitive "contains" filter on the title.
        on_date: only events taking place on this calendar day.
    """
    filters = []
    if title:
        # ``icontains`` -> ILIKE '%...%'. ``autoescape`` makes characters such
        # as % and _ in the user's text match literally, not as wildcards.
        filters.append(Event.title.icontains(title, autoescape=True))
    if on_date is not None:
        start, end = day_bounds(on_date, settings.app_timezone)
        filters.append(Event.event_date >= start)
        filters.append(Event.event_date < end)

    total = await db.scalar(select(func.count(Event.id)).where(*filters)) or 0
    page = await db.scalars(
        select(Event)
        .where(*filters)
        .order_by(Event.event_date, Event.id)  # stable order => stable pages
        .offset(skip)
        .limit(limit)
    )
    return page.all(), total


async def get_event(db: AsyncSession, event_id: int) -> Event:
    """Return one event or raise ``EventNotFoundError``."""
    event = await db.get(Event, event_id)
    if event is None:
        raise EventNotFoundError(event_id)
    return event


async def update_event(db: AsyncSession, event_id: int, data: EventUpdate) -> Event:
    """Apply a partial update: only fields the client actually sent change.

    Capacity cannot drop below the number of employees already registered,
    otherwise the event would be over-booked.
    """
    # Lock the row (SELECT ... FOR UPDATE) so a registration cannot slip in
    # between the capacity check and the commit.
    event = await db.get(Event, event_id, with_for_update=True)
    if event is None:
        raise EventNotFoundError(event_id)

    # ``exclude_unset`` drops fields the client did not send, so they keep
    # their current value instead of being overwritten with ``None``.
    changes = data.model_dump(exclude_unset=True)
    if "max_capacity" in changes:
        registered = await count_registrations(db, event_id)
        if changes["max_capacity"] < registered:
            await db.rollback()  # releases the row lock
            raise CapacityBelowRegistrationsError(changes["max_capacity"], registered)

    for field, value in changes.items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event)
    return event


async def delete_event(db: AsyncSession, event_id: int) -> None:
    """Delete an event (and, via ON DELETE CASCADE, its registrations)."""
    event = await get_event(db, event_id)
    await db.delete(event)
    await db.commit()


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


async def count_registrations(db: AsyncSession, event_id: int) -> int:
    """Return how many employees are registered for an event."""
    query = select(func.count(Registration.id)).where(Registration.event_id == event_id)
    return await db.scalar(query) or 0


async def register_for_event(
    db: AsyncSession, event_id: int, data: RegistrationCreate
) -> Registration:
    """Register an employee for an event, enforcing the three business rules.

    1. The event must exist                         -> ``EventNotFoundError`` (404)
    2. The employee must not already be registered  -> ``DuplicateRegistrationError`` (409)
    3. The event must have a free seat              -> ``EventFullError`` (409)

    Concurrency: two employees could request the last seat at the same time.
    Without care, both would read "1 seat left" and both would be inserted.
    ``SELECT ... FOR UPDATE`` locks the event row, so concurrent registrations
    for the *same* event run one after another and each sees the real count.
    """
    capacity = await db.scalar(
        select(Event.max_capacity).where(Event.id == event_id).with_for_update()
    )
    if capacity is None:
        raise EventNotFoundError(event_id)

    already_registered = await db.scalar(
        select(Registration.id).where(
            Registration.event_id == event_id,
            Registration.employee_email == data.employee_email,
        )
    )
    if already_registered is not None:
        await db.rollback()
        raise DuplicateRegistrationError(data.employee_email, event_id)

    if await count_registrations(db, event_id) >= capacity:
        await db.rollback()
        raise EventFullError(event_id, capacity)

    registration = Registration(event_id=event_id, **data.model_dump())
    db.add(registration)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Safety net: the UNIQUE(event_id, employee_email) constraint fired.
        await db.rollback()
        raise DuplicateRegistrationError(data.employee_email, event_id) from exc
    await db.refresh(registration)
    return registration


async def list_registrations(
    db: AsyncSession, event_id: int, *, skip: int = 0, limit: int = 10
) -> tuple[Sequence[Registration], int]:
    """Return one page of an event's registrations (oldest first) and the total."""
    await get_event(db, event_id)  # raises 404 for an unknown event
    total = await count_registrations(db, event_id)
    query = (
        select(Registration)
        .where(Registration.event_id == event_id)
        .order_by(Registration.registered_at, Registration.id)
        .offset(skip)
        .limit(limit)
    )
    return (await db.scalars(query)).all(), total


async def cancel_registration(db: AsyncSession, registration_id: int) -> None:
    """Cancel (delete) a registration, freeing its seat."""
    registration = await db.get(Registration, registration_id)
    if registration is None:
        raise RegistrationNotFoundError(registration_id)
    await db.delete(registration)
    await db.commit()
