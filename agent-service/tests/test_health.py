"""Tests for the liveness and readiness endpoints."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.health import HealthResponse, ReadinessResponse
from app.main import create_app
from tests.conftest import FakeConsumer, FakeDatabase


def test_health_returns_200(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_health_reports_the_service_as_up(client: TestClient) -> None:
    response = client.get("/health")

    assert response.json() == {
        "status": "UP",
        "service": "reconai-investigation-service",
    }


def test_health_response_matches_its_schema(client: TestClient) -> None:
    body = HealthResponse.model_validate(client.get("/health").json())

    assert body.status == "UP"
    assert body.service == "reconai-investigation-service"


def test_ready_returns_200(client: TestClient) -> None:
    assert client.get("/ready").status_code == 200


def test_ready_reports_the_service_as_ready(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.json() == {
        "status": "READY",
        "service": "reconai-investigation-service",
        "kafka_consumer": "RUNNING",
        "database": "UP",
    }


def test_ready_response_matches_its_schema(client: TestClient) -> None:
    body = ReadinessResponse.model_validate(client.get("/ready").json())

    assert body.status == "READY"
    assert body.service == "reconai-investigation-service"
    assert body.kafka_consumer == "RUNNING"
    assert body.database == "UP"


def test_endpoints_return_json(client: TestClient) -> None:
    for path in ("/health", "/ready"):
        assert client.get(path).headers["content-type"] == "application/json"


def test_service_name_in_responses_comes_from_configuration() -> None:
    renamed = Settings(service_name="renamed-service", environment="test")

    # A fake consumer is passed explicitly: without one this would construct the
    # real Kafka consumer and the test would depend on a running broker.
    with TestClient(create_app(renamed, consumer=FakeConsumer(), database=FakeDatabase())) as client:
        assert client.get("/health").json()["service"] == "renamed-service"
        assert client.get("/ready").json()["service"] == "renamed-service"


def test_unknown_paths_return_404(client: TestClient) -> None:
    assert client.get("/does-not-exist").status_code == 404


def test_no_business_endpoints_are_exposed_yet(client: TestClient) -> None:
    """Phase 4.1 is a scaffold: health and readiness are the whole surface."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {"/health", "/ready"} <= paths
    assert paths == {
        "/health",
        "/ready",
        "/api/v1/investigations",
        "/api/v1/investigations/{investigation_id}",
        "/api/v1/investigations/{investigation_id}/run",
    }
