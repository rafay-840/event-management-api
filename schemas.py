"""Pydantic schemas: what the API accepts and what it returns.

Schemas are kept separate from the ORM models on purpose:

* *Create* schemas describe and validate incoming request bodies.
* *Update* schemas make every field optional (partial update).
* *Response* schemas control exactly which fields leave the API.

If a request body breaks any rule below, FastAPI answers with
``422 Unprocessable Entity`` before our code even runs.
"""

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    model_validator,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TITLE_FIELD = Field(min_length=1, max_length=200, examples=["Intro to FastAPI Workshop"])
LOCATION_FIELD = Field(min_length=1, max_length=200, examples=["Conference Room B, 3rd floor"])
DESCRIPTION_FIELD = Field(
    default=None, max_length=5000, examples=["Hands-on session on building REST APIs."]
)
CAPACITY_FIELD = Field(gt=0, le=100_000, examples=[30], description="Must be greater than zero.")


def validate_event_date(value: datetime) -> datetime:
    """Normalise an event date to UTC-aware and require it to be in the future.

    A date without a time zone (e.g. ``2026-11-20T10:00:00``) is treated as UTC
    so that every value stored in the database is unambiguous.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value <= datetime.now(UTC):
        raise ValueError("event_date must be in the future")
    return value


# A datetime type that runs ``validate_event_date`` after normal parsing.
FutureDatetime = Annotated[datetime, AfterValidator(validate_event_date)]

# A validated e-mail stored in lower case, so "Ana@Acme.com" and
# "ana@acme.com" are recognised as the same person.
LowercaseEmail = Annotated[EmailStr, AfterValidator(str.lower)]


class StrictModel(BaseModel):
    """Base for request schemas.

    * ``str_strip_whitespace`` turns ``"   "`` into ``""`` so that
      ``min_length=1`` also rejects titles made only of spaces.
    * ``extra="forbid"`` rejects unknown fields instead of silently ignoring
      them, so typos in a request body are reported to the client.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventCreate(StrictModel):
    """Request body for ``POST /events``."""

    title: str = TITLE_FIELD
    description: str | None = DESCRIPTION_FIELD
    event_date: FutureDatetime = Field(examples=["2026-12-01T10:00:00+01:00"])
    location: str = LOCATION_FIELD
    max_capacity: int = CAPACITY_FIELD


class EventUpdate(StrictModel):
    """Request body for ``PUT /events/{id}``.

    Every field is optional: only the fields sent by the client are changed.
    Sending ``null`` for a required column is rejected.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    event_date: FutureDatetime | None = None
    location: str | None = Field(default=None, min_length=1, max_length=200)
    max_capacity: int | None = Field(default=None, gt=0, le=100_000)

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        """Require at least one field and forbid ``null`` on required columns."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        for name in ("title", "event_date", "location", "max_capacity"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class EventResponse(BaseModel):
    """An event as returned by the API."""

    # ``from_attributes`` lets Pydantic read values straight from ORM objects.
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    event_date: datetime
    location: str
    max_capacity: int
    registration_count: int = Field(description="Seats already taken.")
    created_at: datetime
    updated_at: datetime

    @computed_field(description="Seats still free.")  # type: ignore[prop-decorator]
    @property
    def available_seats(self) -> int:
        """Remaining capacity; never negative."""
        return max(self.max_capacity - self.registration_count, 0)


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


class RegistrationCreate(StrictModel):
    """Request body for ``POST /events/{id}/registrations``.

    The employee is identified by e-mail, which must be a valid address.
    """

    employee_name: str = Field(min_length=1, max_length=120, examples=["Ana Rossi"])
    employee_email: LowercaseEmail = Field(examples=["ana.rossi@acme.com"])


class RegistrationResponse(BaseModel):
    """A registration as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    employee_name: str
    employee_email: str
    registered_at: datetime


# ---------------------------------------------------------------------------
# Users & authentication
# ---------------------------------------------------------------------------


class UserCreate(StrictModel):
    """Request body for ``POST /auth/register``."""

    email: LowercaseEmail = Field(examples=["ana.rossi@acme.com"])
    full_name: str = Field(min_length=1, max_length=120, examples=["Ana Rossi"])
    # bcrypt only uses the first 72 bytes of a password, so we cap the length.
    password: str = Field(min_length=8, max_length=64, examples=["S3cure-pass!"])


class UserResponse(BaseModel):
    """A user as returned by the API (never includes the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    created_at: datetime


class Token(BaseModel):
    """Response of ``POST /auth/login`` (OAuth2 bearer token format)."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")


class ErrorResponse(BaseModel):
    """Shape of every error produced by our own business rules."""

    detail: str = Field(examples=["Event with id 42 not found"])
