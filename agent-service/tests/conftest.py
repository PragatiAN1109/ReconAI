"""Shared test fixtures.

Nothing here reaches outside the process: no Docker, no Kafka broker, no
database, no network and no model provider. The Kafka boundary is faked, so the
whole suite runs on a laptop with nothing else installed.
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
def client(settings: Settings, consumer: FakeConsumer) -> TestClient:
    """A client whose lifespan runs, so consumer startup is exercised too."""
    with TestClient(create_app(settings, consumer=consumer)) as test_client:
        yield test_client
