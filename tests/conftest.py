"""Shared pytest fixtures.

The tests run against a **real PostgreSQL test database** (not SQLite), so
PostgreSQL-specific behaviour such as ``SELECT ... FOR UPDATE``, ``ILIKE`` and
time-zone handling is exercised exactly as in production.

Isolation: every test gets freshly created, empty tables, and the app's
``get_db`` dependency is overridden to use the test database.
"""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import models  # noqa: F401  (registers tables on Base.metadata)
from db import Base, get_db
from main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/event_management_test",
)

# The fixtures DROP every table, so refuse to run against a non-test database.
if "test" not in (make_url(TEST_DATABASE_URL).database or ""):
    raise RuntimeError("TEST_DATABASE_URL must point to a database whose name contains 'test'")

TEST_USER = {"email": "tester@acme.com", "full_name": "Test User", "password": "S3cure-pass!"}


def future_date(days: int = 30, hour: int = 10) -> str:
    """Return an ISO datetime ``days`` from now (UTC), always in the future."""
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Create empty tables before a test and drop them afterwards.

    ``NullPool`` opens a new connection each time instead of pooling. Pooled
    asyncpg connections are bound to the event loop that created them, and
    pytest-asyncio gives every test its own loop.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the FastAPI app in-process (no server needed)."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user, log in, and return the ``Authorization`` header."""
    await client.post("/auth/register", json=TEST_USER)
    response = await client.post(
        "/auth/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def event_payload() -> dict[str, Any]:
    """A valid request body for ``POST /events``."""
    return {
        "title": "Intro to FastAPI",
        "description": "Hands-on workshop.",
        "event_date": future_date(),
        "location": "Room B",
        "max_capacity": 3,
    }


EventFactory = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
def create_event(
    client: AsyncClient, auth_headers: dict[str, str], event_payload: dict[str, Any]
) -> EventFactory:
    """Return a helper that creates an event (overriding any fields) and returns its JSON."""

    async def _create(**overrides: Any) -> dict[str, Any]:
        response = await client.post(
            "/events", json={**event_payload, **overrides}, headers=auth_headers
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create


RegisterFn = Callable[..., Awaitable[Any]]


@pytest.fixture
def register(client: AsyncClient) -> RegisterFn:
    """Return a helper that registers an employee and returns the raw response."""

    async def _register(event_id: int, email: str, name: str = "Employee"):
        return await client.post(
            f"/events/{event_id}/registrations",
            json={"employee_name": name, "employee_email": email},
        )

    return _register
