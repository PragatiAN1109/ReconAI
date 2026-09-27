"""Tests for consumer lifecycle and readiness.

The Kafka boundary is faked throughout; no broker is contacted.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeConsumer


def test_the_consumer_starts_with_the_application(settings: Settings) -> None:
    consumer = FakeConsumer()

    with TestClient(create_app(settings, consumer=consumer)):
        assert consumer.start_calls == 1
        assert consumer.is_running

    assert consumer.start_calls == 1


def test_the_consumer_stops_with_the_application(settings: Settings) -> None:
    consumer = FakeConsumer()

    with TestClient(create_app(settings, consumer=consumer)):
        pass

    assert consumer.stop_calls == 1
    assert not consumer.is_running


def test_the_consumer_is_stopped_even_if_it_never_started(settings: Settings) -> None:
    """Shutdown must be safe after a failed startup, or the process hangs on exit."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer)):
        pass

    assert consumer.start_calls == 1
    assert consumer.stop_calls == 1


# ---------------------------------------------------------------------------
# Liveness is independent of Kafka; readiness is not
# ---------------------------------------------------------------------------


def test_ready_reports_ready_when_the_consumer_is_running(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "READY",
        "service": "reconai-investigation-service",
        "kafka_consumer": "RUNNING",
    }


def test_the_application_still_starts_when_the_broker_is_unavailable(settings: Settings) -> None:
    """A broker outage must not crash-loop the process."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer)) as client:
        assert client.get("/health").status_code == 200


def test_health_stays_up_when_kafka_is_unavailable(settings: Settings) -> None:
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "service": "reconai-investigation-service"}


def test_ready_reports_not_ready_when_kafka_is_unavailable(settings: Settings) -> None:
    """Readiness must be truthful: this instance is processing nothing."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "NOT_READY",
        "service": "reconai-investigation-service",
        "kafka_consumer": "NOT_RUNNING",
    }
