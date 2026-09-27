"""Tests for consumer lifecycle and readiness.

The Kafka boundary is faked throughout; no broker is contacted.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeConsumer, FakeDatabase


def test_the_consumer_starts_with_the_application(settings: Settings) -> None:
    consumer = FakeConsumer()

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())):
        assert consumer.start_calls == 1
        assert consumer.is_running

    assert consumer.start_calls == 1


def test_the_consumer_stops_with_the_application(settings: Settings) -> None:
    consumer = FakeConsumer()

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())):
        pass

    assert consumer.stop_calls == 1
    assert not consumer.is_running


def test_the_consumer_is_stopped_even_if_it_never_started(settings: Settings) -> None:
    """Shutdown must be safe after a failed startup, or the process hangs on exit."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())):
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
        "database": "UP",
    }


def test_the_application_still_starts_when_the_broker_is_unavailable(settings: Settings) -> None:
    """A broker outage must not crash-loop the process."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())) as client:
        assert client.get("/health").status_code == 200


def test_health_stays_up_when_kafka_is_unavailable(settings: Settings) -> None:
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "service": "reconai-investigation-service"}


def test_ready_reports_not_ready_when_kafka_is_unavailable(settings: Settings) -> None:
    """Readiness must be truthful: this instance is processing nothing."""
    consumer = FakeConsumer(fail_on_start=True)

    with TestClient(create_app(settings, consumer=consumer, database=FakeDatabase())) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "NOT_READY",
        "service": "reconai-investigation-service",
        "kafka_consumer": "NOT_RUNNING",
        "database": "UP",
    }


# ---------------------------------------------------------------------------
# The database is a dependency now too
# ---------------------------------------------------------------------------


def test_the_database_connects_with_the_application(settings: Settings) -> None:
    database = FakeDatabase()

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)):
        assert database.connect_calls == 1
        assert database.is_connected


def test_the_database_disconnects_with_the_application(settings: Settings) -> None:
    database = FakeDatabase()

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)):
        pass

    assert database.disconnect_calls == 1
    assert not database.is_connected


def test_the_database_is_disconnected_even_if_it_never_connected(settings: Settings) -> None:
    database = FakeDatabase(fail_on_connect=True)

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)):
        pass

    assert database.connect_calls == 1
    assert database.disconnect_calls == 1


def test_the_application_still_starts_when_the_database_is_unavailable(
    settings: Settings,
) -> None:
    database = FakeDatabase(fail_on_connect=True)

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)) as client:
        assert client.get("/health").status_code == 200


def test_health_stays_up_when_the_database_is_unavailable(settings: Settings) -> None:
    database = FakeDatabase(fail_on_connect=True)

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "service": "reconai-investigation-service"}


def test_ready_reports_not_ready_when_the_database_is_unavailable(settings: Settings) -> None:
    database = FakeDatabase(fail_on_connect=True)

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "NOT_READY",
        "service": "reconai-investigation-service",
        "kafka_consumer": "RUNNING",
        "database": "DOWN",
    }


def test_ready_requires_both_kafka_and_the_database(settings: Settings) -> None:
    """Either dependency failing is enough to make the instance not ready."""
    both_down = create_app(
        settings,
        consumer=FakeConsumer(fail_on_start=True),
        database=FakeDatabase(fail_on_connect=True),
    )

    with TestClient(both_down) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["kafka_consumer"] == "NOT_RUNNING"
    assert body["database"] == "DOWN"


def test_ready_reports_not_ready_when_the_database_stops_answering(settings: Settings) -> None:
    """A pool can exist while the server behind it has gone away."""
    database = FakeDatabase()

    with TestClient(create_app(settings, consumer=FakeConsumer(), database=database)) as client:
        assert client.get("/ready").status_code == 200
        database.healthy = False
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "DOWN"
