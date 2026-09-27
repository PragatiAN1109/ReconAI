"""Shared test fixtures.

Nothing here reaches outside the process: no Docker, no Kafka, no database, no
network and no model provider. The scaffold has no such dependencies, and the
tests should stay runnable on a laptop with nothing else installed.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
def client(settings: Settings) -> TestClient:
    """A client whose lifespan runs, so startup logging is exercised too."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
