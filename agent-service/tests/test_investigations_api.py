"""Tests for the investigation endpoints.

The service layer is faked throughout; what is under test here is the HTTP
surface — status codes, response shape, and the claims the API makes about
itself. The real workflow, guardrail and review logic are tested against a
database elsewhere.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.evidence_ledger import EvidenceLedger
from app.guardrails import GuardrailDecision
from app.investigation_agent import UngroundedResultError
from app.investigation_model import InvestigationModelError
from app.investigation_models import InvestigationResult
from app.investigation_workflow import (
    InvestigationNotFound,
    InvestigationNotRunnable,
    WorkflowOutcome,
)
from app.main import create_app
from app.models import InvestigationStatus, ReviewDecision
from app.review_service import AlreadyReviewed, NotAwaitingReview
from app.review_service import InvestigationNotFound as ReviewInvestigationNotFound
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
        self.recommendation: object | None = None
        self.evidence: list[object] = []

    async def list_all(self) -> list[object]:
        return self._investigations

    async def get_recommendation(self, investigation_id: str) -> object | None:
        return self.recommendation

    async def get_evidence(self, recommendation_id: str) -> list[object]:
        return self.evidence

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
# Running an investigation
# ---------------------------------------------------------------------------


def result(
    classification: str = "PROCESSOR_FEE",
    confidence: float = 0.88,
    evidence: list[dict] | None = None,
) -> InvestigationResult:
    return InvestigationResult.model_validate(
        {
            "classification": classification,
            "rootCause": "A settlement processing fee matches the difference.",
            "confidence": confidence,
            "evidence": (
                [{"sourceType": "SETTLEMENT", "reference": "SET-8008"}]
                if evidence is None
                else evidence
            ),
            "recommendedAction": "Review and classify as a processor fee adjustment.",
            "requiresHumanApproval": True,
        }
    )


def ledger_with(settlements: tuple[str, ...] = ("SET-8008",)) -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.transactions.add("TX-10007")
    ledger.settlements.update(settlements)
    return ledger


def outcome(
    investigation_result: InvestigationResult | None = None,
    status: InvestigationStatus = InvestigationStatus.AWAITING_REVIEW,
    reason: str = "Reported confidence 0.8800 meets the review threshold 0.85.",
) -> WorkflowOutcome:
    investigation_result = investigation_result or result()
    return WorkflowOutcome(
        investigation=SimpleNamespace(investigation_id="INV-1001"),
        recommendation=SimpleNamespace(
            recommendation_id="REC-3001",
            investigation_id="INV-1001",
            model_provider="anthropic",
            model_name="claude-sonnet-5",
            prompt_version="v1",
            created_at=CREATED_AT,
        ),
        result=investigation_result,
        ledger=ledger_with(),
        decision=GuardrailDecision(status, reason),
    )


class FakeWorkflow:
    """Stands in for the real workflow. The HTTP surface is what is under test."""

    def __init__(self, returns: WorkflowOutcome | None = None, raises: Exception | None = None):
        self._returns = returns
        self._raises = raises
        self.calls: list[str] = []

    async def run(self, investigation_id: str) -> WorkflowOutcome:
        self.calls.append(investigation_id)
        if self._raises is not None:
            raise self._raises
        return self._returns


def app_with(settings: Settings, **state) -> TestClient:
    app = create_app(settings, consumer=FakeConsumer(), database=FakeDatabase())
    app.state.investigations = FakeInvestigationService([investigation()])
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app)


def test_running_without_a_configured_model_reports_unavailable(api: TestClient) -> None:
    """No provider configured is a 503, not a crash and not a fabricated result."""
    response = api.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 503
    assert "No investigation model is configured" in response.json()["detail"]


def test_running_an_unknown_investigation_returns_404(settings: Settings) -> None:
    workflow = FakeWorkflow(raises=InvestigationNotFound("Investigation INV-9 was not found."))

    with app_with(settings, investigation_workflow=workflow) as client:
        response = client.post("/api/v1/investigations/INV-9/run")

    assert response.status_code == 404


def test_running_returns_the_recommendation_the_guardrail_outcome_and_the_evidence(
    settings: Settings,
) -> None:
    with app_with(settings, investigation_workflow=FakeWorkflow(outcome())) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 200
    body = response.json()
    assert body["investigation_id"] == "INV-1001"
    assert body["status"] == "AWAITING_REVIEW"
    assert "meets the review threshold" in body["guardrail_reason"]

    recommendation = body["recommendation"]
    assert recommendation["recommendation_id"] == "REC-3001"
    assert recommendation["classification"] == "PROCESSOR_FEE"
    assert recommendation["requires_human_approval"] is True
    assert recommendation["evidence"] == [
        {"source_type": "SETTLEMENT", "reference": "SET-8008", "section": None, "excerpt": None}
    ]
    # The application's own record of what tools returned, next to the citations.
    assert body["evidence_retrieved"]["settlements"] == ["SET-8008"]
    assert body["evidence_retrieved"]["feeRules"] == []


def test_a_run_response_never_claims_approval_is_unnecessary(settings: Settings) -> None:
    with app_with(settings, investigation_workflow=FakeWorkflow(outcome())) as client:
        body = client.post("/api/v1/investigations/INV-1001/run").json()

    assert body["recommendation"]["requires_human_approval"] is True
    # A run can only route to review or escalation. It cannot complete anything.
    assert body["status"] != "COMPLETED"


def test_an_escalated_result_says_why(settings: Settings) -> None:
    escalated = outcome(
        result(classification="INSUFFICIENT_EVIDENCE", confidence=0.2, evidence=[]),
        status=InvestigationStatus.ESCALATED,
        reason="The investigation concluded INSUFFICIENT_EVIDENCE, which needs a human.",
    )

    with app_with(settings, investigation_workflow=FakeWorkflow(escalated)) as client:
        body = client.post("/api/v1/investigations/INV-1001/run").json()

    assert body["status"] == "ESCALATED"
    assert "INSUFFICIENT_EVIDENCE" in body["guardrail_reason"]
    assert body["recommendation"]["evidence"] == []


def test_running_an_investigation_that_is_not_pending_returns_409(settings: Settings) -> None:
    """A second run must not produce a second conclusion."""
    workflow = FakeWorkflow(raises=InvestigationNotRunnable("INV-1001", "AWAITING_REVIEW"))

    with app_with(settings, investigation_workflow=workflow) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 409
    assert "AWAITING_REVIEW" in response.json()["detail"]


def test_a_provider_outage_is_503(settings: Settings) -> None:
    workflow = FakeWorkflow(raises=InvestigationModelError("The model could not be reached"))

    with app_with(settings, investigation_workflow=workflow) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 503


def test_a_failed_investigation_returns_no_result(settings: Settings) -> None:
    """An ungrounded or malformed result yields an error, never a placeholder."""
    workflow = FakeWorkflow(
        raises=UngroundedResultError("cited evidence that was never retrieved: FEE_RULE/FR-999")
    )

    with app_with(settings, investigation_workflow=workflow) as client:
        response = client.post("/api/v1/investigations/INV-1001/run")

    assert response.status_code == 422
    assert "FR-999" in response.json()["detail"]
    assert "classification" not in response.text


# ---------------------------------------------------------------------------
# Reading a stored recommendation
# ---------------------------------------------------------------------------


def stored_recommendation(**overrides) -> object:
    values = {
        "recommendation_id": "REC-3001",
        "investigation_id": "INV-1001",
        "classification": "PROCESSOR_FEE",
        "root_cause": "A settlement processing fee matches the difference.",
        "confidence": Decimal("0.8800"),
        "recommended_action": "Review and classify as a processor fee adjustment.",
        "requires_human_approval": True,
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-5",
        "prompt_version": "v1",
        "created_at": CREATED_AT,
    }
    return SimpleNamespace(**(values | overrides))


def stored_evidence() -> list[object]:
    return [
        SimpleNamespace(
            source_type="POLICY_DOCUMENT",
            reference="POL-FEE-001",
            section="Processing fees",
            excerpt="Settlement processing fees are deducted at settlement time.",
        )
    ]


def test_a_recommendation_is_returned_with_its_evidence(settings: Settings) -> None:
    service = FakeInvestigationService([investigation()])
    service.recommendation = stored_recommendation()
    service.evidence = stored_evidence()

    with app_with(settings, investigations=service) as client:
        response = client.get("/api/v1/investigations/INV-1001/recommendation")

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation_id"] == "REC-3001"
    assert body["evidence"][0]["section"] == "Processing fees"
    # The excerpt is stored, so a reviewer reads the words the investigation saw.
    assert "deducted at settlement" in body["evidence"][0]["excerpt"]


def test_a_recommendation_reports_confidence_as_uncalibrated(settings: Settings) -> None:
    """A self-reported number must not be presented as a probability."""
    service = FakeInvestigationService([investigation()])
    service.recommendation = stored_recommendation()

    with app_with(settings, investigations=service) as client:
        body = client.get("/api/v1/investigations/INV-1001/recommendation").json()

    assert "not a calibrated probability" in body["confidence_note"]


def test_confidence_survives_json_without_being_rounded(settings: Settings) -> None:
    service = FakeInvestigationService([investigation()])
    service.recommendation = stored_recommendation(confidence=Decimal("0.8765"))

    with app_with(settings, investigations=service) as client:
        raw = client.get("/api/v1/investigations/INV-1001/recommendation").text

    assert "0.8765" in raw


def test_an_investigation_with_no_recommendation_yet_returns_404(settings: Settings) -> None:
    """No conclusion is an honest 404, not an empty recommendation."""
    with app_with(settings) as client:
        response = client.get("/api/v1/investigations/INV-1001/recommendation")

    assert response.status_code == 404
    assert "no recommendation yet" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------


def stored_review(decision: str = "APPROVED", reviewed_by: str = "ops.analyst") -> object:
    return SimpleNamespace(
        review_id="REV-7001",
        investigation_id="INV-1001",
        recommendation_id="REC-3001",
        decision=decision,
        reviewed_by=reviewed_by,
        comment="Matches the fee schedule.",
        decided_at=CREATED_AT,
    )


class FakeReviewService:
    """Records what it was asked to do, and never calls anything else."""

    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises
        self.calls: list[dict] = []

    async def decide(self, investigation_id, *, decision, reviewed_by, comment=None):
        self.calls.append(
            {
                "investigation_id": investigation_id,
                "decision": decision,
                "reviewed_by": reviewed_by,
                "comment": comment,
            }
        )
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(
            review=stored_review(decision.value, reviewed_by),
            investigation=investigation(),
        )


def test_approving_records_a_human_decision(settings: Settings) -> None:
    reviews = FakeReviewService()

    with app_with(settings, reviews=reviews) as client:
        response = client.post(
            "/api/v1/investigations/INV-1001/approve",
            json={"reviewed_by": "ops.analyst", "comment": "Matches the fee schedule."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVED"
    assert body["reviewed_by"] == "ops.analyst"
    assert reviews.calls == [
        {
            "investigation_id": "INV-1001",
            "decision": ReviewDecision.APPROVED,
            "reviewed_by": "ops.analyst",
            "comment": "Matches the fee schedule.",
        }
    ]


def test_every_review_response_says_the_reviewer_is_unverified(settings: Settings) -> None:
    """A demo must not be mistaken for an access-controlled system."""
    with app_with(settings, reviews=FakeReviewService()) as client:
        for endpoint in ("approve", "reject", "escalate"):
            body = client.post(
                f"/api/v1/investigations/INV-1001/{endpoint}",
                json={"reviewed_by": "ops.analyst"},
            ).json()
            assert "unverified" in body["reviewer_note"]
            assert "no authentication" in body["reviewer_note"]


def test_rejecting_records_a_rejection(settings: Settings) -> None:
    reviews = FakeReviewService()

    with app_with(settings, reviews=reviews) as client:
        response = client.post(
            "/api/v1/investigations/INV-1001/reject", json={"reviewed_by": "ops.analyst"}
        )

    assert response.status_code == 200
    assert response.json()["decision"] == "REJECTED"
    assert reviews.calls[0]["decision"] is ReviewDecision.REJECTED


def test_escalating_is_distinct_from_rejecting(settings: Settings) -> None:
    reviews = FakeReviewService()

    with app_with(settings, reviews=reviews) as client:
        client.post(
            "/api/v1/investigations/INV-1001/escalate", json={"reviewed_by": "ops.analyst"}
        )

    assert reviews.calls[0]["decision"] is ReviewDecision.ESCALATED


def test_a_review_must_name_a_reviewer(settings: Settings) -> None:
    """Unauthenticated is not the same as anonymous: someone must be named."""
    reviews = FakeReviewService()

    with app_with(settings, reviews=reviews) as client:
        assert (
            client.post("/api/v1/investigations/INV-1001/approve", json={}).status_code == 422
        )
        assert (
            client.post(
                "/api/v1/investigations/INV-1001/approve", json={"reviewed_by": ""}
            ).status_code
            == 422
        )

    assert reviews.calls == []


def test_reviewing_an_unknown_investigation_returns_404(settings: Settings) -> None:
    reviews = FakeReviewService(
        raises=ReviewInvestigationNotFound("Investigation INV-9 was not found.")
    )

    with app_with(settings, reviews=reviews) as client:
        response = client.post(
            "/api/v1/investigations/INV-9/approve", json={"reviewed_by": "ops.analyst"}
        )

    assert response.status_code == 404


def test_reviewing_something_not_awaiting_review_returns_409(settings: Settings) -> None:
    reviews = FakeReviewService(raises=NotAwaitingReview("INV-1001", "PENDING"))

    with app_with(settings, reviews=reviews) as client:
        response = client.post(
            "/api/v1/investigations/INV-1001/approve", json={"reviewed_by": "ops.analyst"}
        )

    assert response.status_code == 409
    assert "PENDING" in response.json()["detail"]


def test_reviewing_twice_returns_409(settings: Settings) -> None:
    reviews = FakeReviewService(raises=AlreadyReviewed("INV-1001"))

    with app_with(settings, reviews=reviews) as client:
        response = client.post(
            "/api/v1/investigations/INV-1001/approve", json={"reviewed_by": "ops.analyst"}
        )

    assert response.status_code == 409
    assert "already been reviewed" in response.json()["detail"]


def test_there_is_no_endpoint_that_resolves_an_exception(api: TestClient) -> None:
    """Approval is a judgement, not an instruction to act.

    Nothing in this service exposes a way to resolve, settle or adjust a
    financial record — those verbs belong to the financial core, and this
    service only ever reads from it.
    """
    paths = set(api.get("/openapi.json").json()["paths"])

    for verb in ("resolve", "settle", "adjust", "reconcile", "write-off"):
        assert not any(verb in path for path in paths), verb


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


class FakeAuditService:
    def __init__(self, events: list[object] | None = None) -> None:
        self._events = events or []

    async def list_for_investigation(self, investigation_id: str) -> list[object]:
        return [
            event for event in self._events if event.investigation_id == investigation_id
        ]


def audit_event(event_id: str, event_type: str, actor_type: str, **overrides) -> object:
    values = {
        "event_id": event_id,
        "investigation_id": "INV-1001",
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": None,
        "metadata_json": None,
        "occurred_at": CREATED_AT,
    }
    return SimpleNamespace(**(values | overrides))


def test_the_audit_trail_is_returned_in_order(settings: Settings) -> None:
    audit = FakeAuditService(
        [
            audit_event("AUD-9001", "INVESTIGATION_CREATED", "SYSTEM"),
            audit_event("AUD-9002", "INVESTIGATION_STARTED", "SYSTEM"),
            audit_event(
                "AUD-9003",
                "AI_RESULT_GENERATED",
                "AI",
                metadata_json={"classification": "PROCESSOR_FEE", "confidence": "0.8800"},
            ),
            audit_event(
                "AUD-9004", "REVIEW_APPROVED", "HUMAN", actor_id="ops.analyst"
            ),
        ]
    )

    with app_with(settings, audit=audit) as client:
        response = client.get("/api/v1/investigations/INV-1001/audit")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["event_type"] for item in body["items"]] == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
        "AI_RESULT_GENERATED",
        "REVIEW_APPROVED",
    ]
    assert [item["actor_type"] for item in body["items"]] == [
        "SYSTEM",
        "SYSTEM",
        "AI",
        "HUMAN",
    ]
    assert body["items"][3]["actor_id"] == "ops.analyst"
    # Confidence travels as a string, so JSON cannot round it.
    assert body["items"][2]["metadata"]["confidence"] == "0.8800"


def test_the_audit_trail_of_an_unknown_investigation_is_404(settings: Settings) -> None:
    with app_with(settings, audit=FakeAuditService()) as client:
        assert client.get("/api/v1/investigations/INV-999999/audit").status_code == 404


def test_an_audit_trail_can_be_empty(settings: Settings) -> None:
    with app_with(settings, audit=FakeAuditService()) as client:
        body = client.get("/api/v1/investigations/INV-1001/audit").json()

    assert body == {"investigation_id": "INV-1001", "items": [], "total": 0}


def test_the_audit_trail_cannot_be_written_or_deleted_through_the_api(
    settings: Settings,
) -> None:
    """Append-only is enforced by there being no endpoint, not by a check."""
    with app_with(settings, audit=FakeAuditService()) as client:
        assert client.post("/api/v1/investigations/INV-1001/audit", json={}).status_code == 405
        assert client.put("/api/v1/investigations/INV-1001/audit", json={}).status_code == 405
        assert client.delete("/api/v1/investigations/INV-1001/audit").status_code == 405
