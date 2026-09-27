"""Tests for the read-only investigation endpoints.

The service layer is faked; what is under test is the HTTP surface.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeConsumer, FakeDatabase

DETECTED_AT = datetime(2026, 9, 27, 2, 4, 16, 954772, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 27, 2, 4, 17, tzinfo=UTC)


def investigation(investigation_id: str = "INV-1001", exception_id: str = "EX-1006") -> object:
    return SimpleNamespace(
        id="b1c1d1e1-0000-0000-0000-000000000001",
        investigation_id=investigation_id,
        exception_id=exception_id,
        transaction_id="TX-10007",
        exception_type="AMOUNT_MISMATCH",
        status="PENDING",
        detected_at=DETECTED_AT,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )


class FakeInvestigationService:
    def __init__(self, investigations: list[object] | None = None) -> None:
        self._investigations = investigations or []

    async def list_all(self) -> list[object]:
        return self._investigations

    async def get_by_investigation_id(self, investigation_id: str) -> object | None:
        return next(
            (
                found
                for found in self._investigations
                if found.investigation_id == investigation_id
            ),
            None,
        )


@pytest.fixture
def api(settings: Settings) -> TestClient:
    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([investigation()])
    with TestClient(app) as client:
        yield client


def test_listing_returns_the_stored_investigations(api: TestClient) -> None:
    response = api.get("/api/v1/investigations")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["investigation_id"] == "INV-1001"
    assert body["items"][0]["exception_id"] == "EX-1006"
    assert body["items"][0]["status"] == "PENDING"


def test_an_investigation_can_be_fetched_by_business_id(api: TestClient) -> None:
    response = api.get("/api/v1/investigations/INV-1001")

    assert response.status_code == 200
    assert response.json() == {
        "investigation_id": "INV-1001",
        "exception_id": "EX-1006",
        "transaction_id": "TX-10007",
        "exception_type": "AMOUNT_MISMATCH",
        "status": "PENDING",
        "detected_at": "2026-09-27T02:04:16.954772Z",
        "created_at": "2026-09-27T02:04:17Z",
        "updated_at": "2026-09-27T02:04:17Z",
    }


def test_an_unknown_investigation_returns_404(api: TestClient) -> None:
    response = api.get("/api/v1/investigations/INV-999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Investigation INV-999999 was not found."


def test_the_internal_uuid_is_not_exposed(api: TestClient) -> None:
    body = api.get("/api/v1/investigations/INV-1001").text

    assert "b1c1d1e1" not in body
    assert '"id"' not in body


def test_an_empty_store_lists_nothing(settings: Settings) -> None:
    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([])

    with TestClient(app) as client:
        response = client.get("/api/v1/investigations")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_investigations_cannot_be_created_through_the_api(api: TestClient) -> None:
    """Kafka is the only creation path; a caller must not assert one into being."""
    assert api.post("/api/v1/investigations", json={}).status_code == 405
    assert api.put("/api/v1/investigations/INV-1001", json={}).status_code == 405
    assert api.delete("/api/v1/investigations/INV-1001").status_code == 405


# ---------------------------------------------------------------------------
# The investigation entry point
# ---------------------------------------------------------------------------


def test_running_an_unknown_investigation_returns_404(api: TestClient) -> None:
    assert api.post("/api/v1/investigations/INV-999999/run").status_code == 404


def test_running_without_a_configured_model_reports_unavailable(api: TestClient) -> None:
    """No provider configured is a 503, not a crash and not a fabricated result."""
    response = api.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 503
    assert "No investigation model is configured" in response.json()["detail"]


def test_running_an_investigation_returns_the_result_and_the_evidence_behind_it(
    settings: Settings,
) -> None:
    from app.investigation_models import InvestigationResult  # noqa: PLC0415

    class StubAgent:
        async def investigate(self, context):
            from app.evidence_ledger import EvidenceLedger  # noqa: PLC0415

            ledger = EvidenceLedger()
            ledger.transactions.add("TX-10007")
            ledger.settlements.add("SET-8008")
            result = InvestigationResult.model_validate(
                {
                    "classification": "PROCESSOR_FEE",
                    "rootCause": "A settlement processing fee matches the difference.",
                    "confidence": 0.88,
                    "evidence": [{"sourceType": "SETTLEMENT", "reference": "SET-8008"}],
                    "recommendedAction": "Review and classify as a processor fee adjustment.",
                    "requiresHumanApproval": True,
                }
            )
            return result, ledger

    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([investigation()])
    app.state.investigation_agent = StubAgent()

    with TestClient(app) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 200
    body = response.json()
    assert body["investigation_id"] == "INV-1001"
    assert body["result"]["classification"] == "PROCESSOR_FEE"
    assert body["result"]["requiresHumanApproval"] is True
    # The application's own record of what tools returned, reported next to the
    # model's citations.
    assert body["evidence_retrieved"]["settlements"] == ["SET-8008"]
    assert body["evidence_retrieved"]["feeRules"] == []


def test_a_failed_investigation_returns_no_result(settings: Settings) -> None:
    """An ungrounded or malformed result yields an error, never a placeholder."""
    from app.investigation_agent import UngroundedResultError  # noqa: PLC0415

    class FailingAgent:
        async def investigate(self, context):
            raise UngroundedResultError("cited evidence that was never retrieved: FEE_RULE/FR-999")

    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([investigation()])
    app.state.investigation_agent = FailingAgent()

    with TestClient(app) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 422
    assert "FR-999" in response.json()["detail"]
    assert "classification" not in response.text


def test_running_an_investigation_does_not_change_it(settings: Settings) -> None:
    """Phase 4.6 does not persist results or advance the lifecycle."""
    stored = investigation()

    class StubAgent:
        async def investigate(self, context):
            from app.evidence_ledger import EvidenceLedger  # noqa: PLC0415
            from app.investigation_models import InvestigationResult  # noqa: PLC0415

            return (
                InvestigationResult.model_validate(
                    {
                        "classification": "INSUFFICIENT_EVIDENCE",
                        "rootCause": "Nothing accounts for the difference.",
                        "confidence": 0.1,
                        "evidence": [],
                        "recommendedAction": "Escalate for manual review.",
                        "requiresHumanApproval": True,
                    }
                ),
                EvidenceLedger(),
            )

    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([stored])
    app.state.investigation_agent = StubAgent()

    with TestClient(app) as client:
        assert client.post("/api/v1/investigations/INV-1001/run").status_code == 200
        assert client.get("/api/v1/investigations/INV-1001").json()["status"] == "PENDING"

    assert stored.status == "PENDING"
