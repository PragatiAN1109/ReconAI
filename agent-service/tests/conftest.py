"""Shared test fixtures.

Two kinds of test live here. Most fake the Kafka and database boundaries and
run with nothing installed — no broker, no database, no network, no model
provider. A smaller set is marked ``integration`` and uses a real PostgreSQL
through Testcontainers, because unique constraints under concurrency, sequences
and transaction behaviour cannot be proven against a substitute.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeConsumer:
    """Stands in for the Kafka consumer at the lifecycle boundary.

    Mirrors the three things the application actually uses — ``start``,
    ``stop`` and ``is_running`` — and records that they happened, so tests can
    assert lifecycle behaviour without a broker.
    """

    def __init__(self, *, fail_on_start: bool = False) -> None:
        self.fail_on_start = fail_on_start
        self.start_calls = 0
        self.stop_calls = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self.start_calls += 1
        if self.fail_on_start:
            raise ConnectionError("simulated broker unavailable")
        self._running = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self._running = False


class FakeDatabase:
    """Stands in for the database at the lifecycle and readiness boundaries."""

    def __init__(self, *, fail_on_connect: bool = False, healthy: bool = True) -> None:
        self.fail_on_connect = fail_on_connect
        self.healthy = healthy
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.fail_on_connect:
            raise ConnectionError("simulated database unavailable")
        self._connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    async def check(self) -> bool:
        return self._connected and self.healthy


@pytest.fixture
def settings() -> Settings:
    """Explicit settings, so a stray environment variable cannot change a result."""
    return Settings(
        service_name="reconai-investigation-service",
        environment="test",
        host="127.0.0.1",
        port=8000,
        log_level="INFO",
    )


@pytest.fixture
def consumer() -> FakeConsumer:
    return FakeConsumer()


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(settings: Settings, consumer: FakeConsumer, database: FakeDatabase) -> TestClient:
    """A client whose lifespan runs, so startup and shutdown are exercised too."""
    with TestClient(create_app(settings, consumer=consumer, database=database)) as test_client:
        yield test_client
