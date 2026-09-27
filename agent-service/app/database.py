"""Database engine and session lifecycle."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings

logger = logging.getLogger(__name__)


class Database:
    """Owns the connection pool and hands out sessions.

    Schema creation is Alembic's job, never this class's: a service that
    creates its own tables at startup has no reviewable history of how the
    schema got to its current shape.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def is_connected(self) -> bool:
        """True once the engine has been created and verified reachable."""
        return self._engine is not None

    async def connect(self) -> None:
        """Create the pool and confirm the database answers.

        The connectivity check happens once, here, rather than on every
        readiness call. Raises if the database cannot be reached, so the caller
        decides whether that is fatal.
        """
        engine = create_async_engine(
            self._settings.database_url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
        )
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        logger.info("Database connected [url=%s]", self._settings.redacted_database_url)

    async def disconnect(self) -> None:
        """Close the pool. Safe whether or not ``connect`` succeeded."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database disconnected")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session that commits on success and rolls back on failure."""
        if self._session_factory is None:
            raise RuntimeError("Database is not connected")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check(self) -> bool:
        """Cheap liveness probe against the pool, for readiness.

        ``SELECT 1`` on a pooled connection, so a healthy service is not paying
        for a new connection on every readiness poll.
        """
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("Database connectivity check failed", exc_info=True)
            return False
