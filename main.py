"""API endpoints and application entry point.

Routes in this module are intentionally thin. Each one:

1. lets FastAPI validate the input (path/query parameters, Pydantic body),
2. calls one function in ``services.py``,
3. returns the result, which FastAPI serialises with ``response_model``.

Business-rule failures are raised as domain exceptions by the service layer
and converted to JSON error responses by the exception handlers below, so no
route needs its own ``try/except``.

Authentication: reading events and registering for them is public; creating,
updating and deleting events requires a JWT (see ``auth.py``).

Run with:  ``uvicorn main:app --reload``  then open http://127.0.0.1:8000/docs
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

import services
from auth import CurrentUser, create_access_token, get_current_user
from db import DEFAULT_JWT_SECRET, get_db, init_db, settings
from schemas import (
    ErrorResponse,
    EventCreate,
    EventResponse,
    EventUpdate,
    RegistrationCreate,
    RegistrationResponse,
    Token,
    UserCreate,
    UserResponse,
)

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the database tables once, when the server starts."""
    if settings.jwt_secret_key == DEFAULT_JWT_SECRET:
        logger.warning("JWT_SECRET_KEY is the insecure default. Set it in your .env file.")
    await init_db()
    yield


TAGS_METADATA = [
    {"name": "Auth", "description": "Sign up, log in and get a JWT access token."},
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

# Added to a route's ``dependencies`` to require a valid Bearer token.
REQUIRE_AUTH = [Depends(get_current_user)]

# Shorthand types used in route signatures.
DbSession = Annotated[AsyncSession, Depends(get_db)]
EventId = Annotated[int, Path(gt=0, description="The event's unique id.")]
RegistrationId = Annotated[int, Path(gt=0, description="The registration's unique id.")]

# Documented error responses, reused by several routes in the OpenAPI schema.
NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"}}
CONFLICT = {status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Conflict"}}
UNAUTHORIZED = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Missing or invalid token"}
}

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


@app.exception_handler(services.AuthenticationError)
async def authentication_handler(_: Request, exc: services.AuthenticationError) -> JSONResponse:
    """Translate failed authentication into ``401 Unauthorized``."""
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": exc.message},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(services.ConflictError)
async def conflict_handler(_: Request, exc: services.ConflictError) -> JSONResponse:
    """Translate a business-rule clash into ``409 Conflict``."""
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": exc.message})


# ---------------------------------------------------------------------------
# Root & authentication
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send visitors of the bare URL to the demo frontend."""
    return RedirectResponse(url="/app/")


@app.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses=CONFLICT,
    tags=["Auth"],
    summary="Create an account",
)
async def register_user(payload: UserCreate, db: DbSession) -> UserResponse:
    """Create a user account. Returns 409 if the e-mail is already taken."""
    return await services.create_user(db, payload)


@app.post(
    "/auth/login",
    response_model=Token,
    responses=UNAUTHORIZED,
    tags=["Auth"],
    summary="Log in and get an access token",
)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> Token:
    """Exchange e-mail and password for a JWT.

    Uses the standard OAuth2 *password* form (`username` = your e-mail), which
    is what Swagger's **Authorize** button sends.
    """
    user = await services.authenticate_user(db, form.username, form.password)
    return Token(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@app.get(
    "/auth/me",
    response_model=UserResponse,
    responses=UNAUTHORIZED,
    tags=["Auth"],
    summary="Get the logged-in user",
)
async def read_current_user(current_user: CurrentUser) -> UserResponse:
    """Return the account that owns the token (handy to test a token)."""
    return current_user


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@app.post(
    "/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    responses=UNAUTHORIZED,
    dependencies=REQUIRE_AUTH,
    tags=["Events"],
    summary="Create a new event (auth required)",
)
async def create_event(payload: EventCreate, db: DbSession) -> EventResponse:
    """Create an event. Title, date (in the future), location and capacity are required.

    Requires a Bearer token.
    """
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
    responses={**UNAUTHORIZED, **NOT_FOUND, **CONFLICT},
    dependencies=REQUIRE_AUTH,
    tags=["Events"],
    summary="Update an event (auth required)",
)
async def update_event(event_id: EventId, payload: EventUpdate, db: DbSession) -> EventResponse:
    """Update an event. Only the fields included in the body are changed. Requires a Bearer token.

    Returns 409 if `max_capacity` would drop below the seats already taken.
    """
    return await services.update_event(db, event_id, payload)


@app.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**UNAUTHORIZED, **NOT_FOUND},
    dependencies=REQUIRE_AUTH,
    tags=["Events"],
    summary="Delete an event (auth required)",
)
async def delete_event(event_id: EventId, db: DbSession) -> None:
    """Delete an event and all its registrations. Returns 204. Requires a Bearer token."""
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


# ---------------------------------------------------------------------------
# Optional demo frontend (plain HTML/JS) served at /app
# ---------------------------------------------------------------------------

FRONTEND_DIR = FilePath(__file__).parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
