"""Database connection and application configuration.

This module is the single place where the application learns *where* its
database lives and *how* it talks to it:

* ``Settings`` reads configuration from environment variables (or a ``.env``
  file) so that credentials never have to be hard-coded.
* ``engine`` is the SQLAlchemy 2.0 **async** engine. It owns a pool of
  connections to PostgreSQL through the ``asyncpg`` driver.
* ``AsyncSessionLocal`` is a factory that hands out ``AsyncSession`` objects,
  one per HTTP request.
* ``get_db`` is the FastAPI dependency that opens a session for a request and
  always closes it afterwards, even if the request fails.
* ``init_db`` creates the tables at start-up.
"""

from collections.abc import AsyncIterator

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Settings(BaseSettings):
    """Application settings loaded from environment variables / ``.env``.

    Every attribute can be overridden by an environment variable with the
    same name in upper case, e.g. ``DATABASE_URL``.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ``postgresql+asyncpg`` tells SQLAlchemy to use the async asyncpg driver.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/event_management"

    # When True, SQLAlchemy logs every SQL statement (useful while debugging).
    sql_echo: bool = False


settings = Settings()

# The engine is created once per process and shared by every request.
# ``pool_pre_ping`` checks a pooled connection is still alive before using it,
# which avoids errors after the database restarts.
engine = create_async_engine(settings.database_url, echo=settings.sql_echo, pool_pre_ping=True)

# ``expire_on_commit=False`` keeps ORM objects usable after ``commit()``.
# In async code this matters: an expired attribute would trigger an implicit
# (synchronous) reload, which is not allowed with AsyncSession.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for every ORM model; it collects the table metadata."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide one database session per request (FastAPI dependency).

    The ``async with`` block guarantees the session is closed and its
    connection returned to the pool when the request finishes.
    """
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables that do not exist yet.

    ``create_all`` is a synchronous SQLAlchemy API, so it is executed through
    ``run_sync`` on an async connection. For a production system you would use
    migrations (Alembic) instead, because ``create_all`` never alters existing
    tables.
    """
    import models  # noqa: F401  (importing registers the models on Base.metadata)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
