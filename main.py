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
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import services
from db import get_db, init_db
from schemas import ErrorResponse, EventCreate, EventResponse, EventUpdate


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the database tables once, when the server starts."""
    await init_db()
    yield


TAGS_METADATA = [
    {"name": "Events", "description": "Create, read, update and delete company events."},
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

# Documented error responses, reused by several routes in the OpenAPI schema.
NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"}}


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


@app.get("/events", response_model=list[EventResponse], tags=["Events"], summary="List events")
async def list_events(db: DbSession) -> list[EventResponse]:
    """Return every event, ordered by date."""
    return await services.list_events(db)


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
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Update an event",
)
async def update_event(event_id: EventId, payload: EventUpdate, db: DbSession) -> EventResponse:
    """Update an event. Only the fields included in the body are changed."""
    return await services.update_event(db, event_id, payload)


@app.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    tags=["Events"],
    summary="Delete an event",
)
async def delete_event(event_id: EventId, db: DbSession) -> None:
    """Delete an event. Returns 204 with an empty body on success."""
    await services.delete_event(db, event_id)
