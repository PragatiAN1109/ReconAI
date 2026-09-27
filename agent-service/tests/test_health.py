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


def test_the_exposed_surface_is_exactly_what_is_intended(client: TestClient) -> None:
    """An exhaustive inventory, so a new endpoint cannot appear unnoticed.

    This test is meant to fail whenever the surface changes. Updating it is how
    adding an endpoint becomes a deliberate act rather than a side effect.
    """
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {"/health", "/ready"} <= paths
    assert paths == {
        "/health",
        "/ready",
        "/api/v1/investigations",
        "/api/v1/investigations/{investigation_id}",
        "/api/v1/investigations/{investigation_id}/recommendation",
        "/api/v1/investigations/{investigation_id}/audit",
        "/api/v1/investigations/{investigation_id}/run",
        "/api/v1/investigations/{investigation_id}/approve",
        "/api/v1/investigations/{investigation_id}/reject",
        "/api/v1/investigations/{investigation_id}/escalate",
    }


def test_no_endpoint_can_mutate_a_financial_record(client: TestClient) -> None:
    """Every path here belongs to this service's own resources.

    The financial core is read-only to this service. Nothing in the surface
    addresses a transaction, a settlement or an exception, so there is no
    endpoint a client could mistake for one that writes to it.
    """
    paths = set(client.get("/openapi.json").json()["paths"])

    business_paths = {path for path in paths if path.startswith("/api/")}
    assert business_paths, "expected business endpoints to exist"
    for path in business_paths:
        assert path.startswith("/api/v1/investigations"), path
    assert not any(
        segment in path
        for path in paths
        for segment in ("/transactions", "/settlements", "/exceptions", "/fee-rules")
    )
