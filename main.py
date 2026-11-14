"""API endpoints and application entry point.

Routes in this module are intentionally thin. Each one:

1. lets FastAPI validate the input (path/query parameters, Pydantic body),
2. calls one function in ``services.py``,
3. returns the result, which FastAPI serialises with ``response_model``.

Business-rule failures are raised as domain exceptions by the service layer
and converted to JSON error responses by the exception handlers below, so no
route needs its own ``try/except``.

Run with:  ``uvicorn main:app --reload``  then open http://127.0.0.1:8000/docs
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import services
from db import get_db, init_db
from schemas import (
    ErrorResponse,
    EventCreate,
    EventResponse,
    EventUpdate,
    RegistrationCreate,
    RegistrationResponse,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the database tables once, when the server starts."""
    await init_db()
    yield


TAGS_METADATA = [
    {"name": "Events", "description": "Create, read, update and delete company events."},
    {"name": "Registrations", "description": "Register employees for events and cancel seats."},
]

app = FastAPI(
    title="Corporate Event Management API",
    description=(
        "Internal API for Acme Corp to manage events (workshops, product launches, "
        "team-building activities, conferences) and employee registrations."
    ),
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)

# Shorthand types used in route signatures.
DbSession = Annotated[AsyncSession, Depends(get_db)]
EventId = Annotated[int, Path(gt=0, description="The event's unique id.")]
RegistrationId = Annotated[int, Path(gt=0, description="The registration's unique id.")]

# Documented error responses, reused by several routes in the OpenAPI schema.
NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"}}
CONFLICT = {status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Conflict"}}

# Pagination parameters shared by every list endpoint.
Skip = Annotated[int, Query(ge=0, description="Number of records to skip.")]
Limit = Annotated[int, Query(ge=1, le=100, description="Maximum records to return (1-100).")]

TOTAL_COUNT_HEADER = "X-Total-Count"


# ---------------------------------------------------------------------------
# Exception handlers: domain exceptions -> HTTP responses
# ---------------------------------------------------------------------------


@app.exception_handler(services.NotFoundError)
async def not_found_handler(_: Request, exc: services.NotFoundError) -> JSONResponse:
    """Translate a missing resource into ``404 Not Found``."""
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": exc.message})


@app.exception_handler(services.ConflictError)
async def conflict_handler(_: Request, exc: services.ConflictError) -> JSONResponse:
    """Translate a business-rule clash into ``409 Conflict``."""
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": exc.message})


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@app.post(
    "/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Events"],
    summary="Create a new event",
)
async def create_event(payload: EventCreate, db: DbSession) -> EventResponse:
    """Create an event. Title, date (in the future), location and capacity are required."""
    return await services.create_event(db, payload)


@app.get(
    "/events",
    response_model=list[EventResponse],
    tags=["Events"],
    summary="List, search and filter events",
)
async def list_events(
    response: Response,
    db: DbSession,
    skip: Skip = 0,
    limit: Limit = 10,
    title: Annotated[
        str | None,
        Query(min_length=1, max_length=200, description="Case-insensitive part of the title."),
    ] = None,
    event_date: Annotated[
        date | None,
        Query(alias="date", description="Only events on this day (YYYY-MM-DD)."),
    ] = None,
) -> list[EventResponse]:
    """Return events ordered by date, one page at a time.

    Filters can be combined with each other and with pagination, e.g.
    `/events?title=python&date=2026-11-20&skip=0&limit=10`.
    The total number of matching events is sent in the `X-Total-Count` header.
    """
    events, total = await services.list_events(
        db, skip=skip, limit=limit, title=title, on_date=event_date
    )
    response.headers[TOTAL_COUNT_HEADER] = str(total)
    return events


@app.get(
    "/events/{event_id}",
    response_model=EventResponse,
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Get one event",
)
async def get_event(event_id: EventId, db: DbSession) -> EventResponse:
    """Return a single event by id, or 404 if it does not exist."""
    return await services.get_event(db, event_id)


@app.put(
    "/events/{event_id}",
    response_model=EventResponse,
    responses={**NOT_FOUND, **CONFLICT},
    tags=["Events"],
    summary="Update an event",
)
async def update_event(event_id: EventId, payload: EventUpdate, db: DbSession) -> EventResponse:
    """Update an event. Only the fields included in the body are changed.

    Returns 409 if `max_capacity` would drop below the seats already taken.
    """
    return await services.update_event(db, event_id, payload)


@app.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Delete an event",
)
async def delete_event(event_id: EventId, db: DbSession) -> None:
    """Delete an event and all its registrations. Returns 204 with an empty body."""
    await services.delete_event(db, event_id)


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------


@app.post(
    "/events/{event_id}/registrations",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={**NOT_FOUND, **CONFLICT},
    tags=["Registrations"],
    summary="Register for an event",
)
async def register_for_event(
    event_id: EventId, payload: RegistrationCreate, db: DbSession
) -> RegistrationResponse:
    """Register an employee for an event.

    * **404** if the event does not exist
    * **409** if the employee is already registered, or the event is full
    """
    return await services.register_for_event(db, event_id, payload)


@app.get(
    "/events/{event_id}/registrations",
    response_model=list[RegistrationResponse],
    responses=NOT_FOUND,
    tags=["Registrations"],
    summary="List an event's registrations",
)
async def list_registrations(
    event_id: EventId,
    response: Response,
    db: DbSession,
    skip: Skip = 0,
    limit: Limit = 10,
) -> list[RegistrationResponse]:
    """Return the event's registrations in registration order, one page at a time.

    The total number of registrations is sent in the `X-Total-Count` header.
    """
    registrations, total = await services.list_registrations(
        db, event_id, skip=skip, limit=limit
    )
    response.headers[TOTAL_COUNT_HEADER] = str(total)
    return registrations


@app.delete(
    "/registrations/{registration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    tags=["Registrations"],
    summary="Cancel a registration",
)
async def cancel_registration(registration_id: RegistrationId, db: DbSession) -> None:
    """Cancel a registration, freeing one seat. Returns 204 on success."""
    await services.cancel_registration(db, registration_id)
