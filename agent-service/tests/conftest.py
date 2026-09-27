"""Shared test fixtures.

Two kinds of test live here. Most fake the Kafka and database boundaries and
run with nothing installed — no broker, no database, no network, no model
provider. A smaller set is marked ``integration`` and uses a real PostgreSQL
through Testcontainers, because unique constraints under concurrency, sequences
and transaction behaviour cannot be proven against a substitute.
"""

from collections.abc import Iterator

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


class FakeFinancialCore:
    """Stands in for the Financial Core client at the lifecycle boundary."""

    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.is_open = False

    async def open(self) -> None:
        self.open_calls += 1
        self.is_open = True

    async def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


@pytest.fixture
def settings() -> Settings:
    """Explicit settings, so a stray environment variable cannot change a result.

    ``_env_file=None`` is what makes that true, and it is not optional. Without
    it these settings read ``agent-service/.env``, so a developer who has
    configured a real provider gets a different application under test than CI
    does — and tests that assert "no model is configured" fail on their machine
    only. Worse, a test that reached the provider would spend real money.
    """
    return Settings(
        _env_file=None,
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
def financial_core() -> FakeFinancialCore:
    return FakeFinancialCore()


@pytest.fixture
def client(
    settings: Settings,
    consumer: FakeConsumer,
    database: FakeDatabase,
    financial_core: FakeFinancialCore,
) -> TestClient:
    """A client whose lifespan runs, so startup and shutdown are exercised too."""
    app = create_app(settings, consumer=consumer, database=database, financial_core=financial_core)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# A real PostgreSQL, shared by every integration module
# ---------------------------------------------------------------------------


def docker_is_available() -> bool:
    try:
        import docker  # noqa: PLC0415

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_url() -> "Iterator[str]":
    """A throwaway PostgreSQL with this service's schema migrated into it.

    Session-scoped so one container serves every integration module: starting a
    PostgreSQL per module costs far more than the isolation is worth, and each
    module cleans up after itself.

    The real Alembic migration runs here rather than ``create_all``, so these
    tests exercise the schema the application actually deploys — constraints,
    defaults and sequences included — instead of one rebuilt from ORM metadata.
    """
    if not docker_is_available():
        pytest.skip("Docker is unavailable; skipping PostgreSQL integration tests")

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        alembic_config = Config("alembic.ini")
        alembic_config.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_config, "head")
        yield url
