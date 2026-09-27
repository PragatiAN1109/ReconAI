"""The whole lifecycle, end to end, over HTTP against a real PostgreSQL.

Every layer here is the real one except two, and both are replaced at a
boundary that crosses the network:

* the **model provider**, replaced by a scripted fake — the investigation logic
  worth testing (the allowlist, the bounded loop, the ledger, grounding
  validation) is all on our side of that boundary;
* the **financial core**, replaced by an httpx MockTransport serving its real
  read-only response shapes.

Everything between them is production code: the real ``InvestigationAgent``,
the real controlled tools, the real policy corpus on disk, the real guardrail,
the real persistence, the real review service, the real audit trail and the
real FastAPI routes, against a real PostgreSQL with the real migrations applied.

No test in this file makes a network call to any provider, and none requires an
API key.
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from app.audit_service import AuditService
from app.config import Settings
from app.database import Database
from app.investigation_agent import InvestigationAgent
from app.investigation_service import InvestigationService
from app.financial_core_client import FinancialCoreClient
from app.investigation_workflow import InvestigationWorkflow
from app.main import create_app
from app.models import SCHEMA
from app.policy_search import PolicySearch
from app.review_service import ReviewService
from tests.conftest import FakeConsumer
from tests.fake_model import FakeModel, final_turn, tool_turn
from tests.test_investigation_agent import (
    FEE_RULES,
    POLICY_CORPUS,
    SETTLEMENTS,
    TRANSACTION,
    routing_core,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def database(postgres_url: str) -> AsyncIterator[Database]:
    db = Database(Settings(_env_file=None, database_url=postgres_url))
    await db.connect()
    yield db
    await db.disconnect()


@pytest.fixture(autouse=True)
async def clean(database: Database) -> AsyncIterator[None]:
    yield
    async with database.session() as session:
        for table in (
            "recommendation_evidence",
            "reviews",
            "audit_events",
            "recommendations",
            "investigations",
        ):
            await session.execute(sa.text(f"DELETE FROM {SCHEMA}.{table}"))


@pytest.fixture
def policies(tmp_path) -> PolicySearch:
    for name, content in POLICY_CORPUS.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return PolicySearch(tmp_path)


def investigating_model() -> FakeModel:
    """A model that gathers evidence properly, then concludes from it.

    The script mirrors a real investigation: fetch the transaction, fetch its
    settlements, look for a fee rule, check the policy, then submit a result
    citing only what came back.
    """
    return FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            tool_turn("get_settlements", {"transaction_id": "TX-10009"}),
            tool_turn("get_fee_rules", {"merchant_id": "MERCHANT-PHASE43-DEMO"}),
            tool_turn("search_policies", {"query": "settlement processing fee"}),
            final_turn(
                classification="PROCESSOR_FEE",
                confidence=0.91,
                rootCause=(
                    "The settled amount is 50.00 below the expected amount, which "
                    "matches the active processing fee for this merchant and processor."
                ),
                evidence=[
                    {"sourceType": "TRANSACTION", "reference": "TX-10009"},
                    {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
                    {"sourceType": "FEE_RULE", "reference": "FR-14"},
                ],
                recommendedAction="Classify as a processor fee adjustment and close.",
            ),
        ]
    )


@pytest.fixture
def app(database: Database, policies: PolicySearch) -> FastAPI:
    """The real application, wired to a real database and a scripted model."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database._settings.database_url,  # noqa: SLF001
    )
    agent = InvestigationAgent(investigating_model(), routing_core(), policies)

    built = create_app(settings, consumer=FakeConsumer(), database=database)
    built.state.investigations = InvestigationService(database)
    built.state.reviews = ReviewService(database)
    built.state.audit = AuditService(database)
    built.state.investigation_agent = agent
    built.state.investigation_workflow = InvestigationWorkflow(
        database,
        agent,
        confidence_threshold=settings.review_confidence_threshold,
        minimum_evidence=settings.review_minimum_evidence,
        model_provider="fake",
        model_name="scripted-model",
        prompt_version=settings.prompt_version,
    )
    return built


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client, not a TestClient.

    TestClient drives the app through its own event loop in a worker thread.
    The database fixture here is connected on the test's loop, and an asyncpg
    connection cannot be used from two loops — so the whole test runs on one
    loop instead.

    The lifespan is not run: this test supplies the wired dependencies itself,
    and startup would otherwise open a second engine for the same database.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://investigation.test"
    ) as client:
        yield client


async def given_an_exception(database: Database, exception_id: str = "EX-9001") -> str:
    """Record an investigation the way Kafka does, through the real service."""
    from app.events import ReconciliationExceptionEvent  # noqa: PLC0415

    record = await InvestigationService(database).create_or_get(
        ReconciliationExceptionEvent.model_validate(
            {
                "exceptionId": exception_id,
                "transactionId": "TX-10009",
                "type": "AMOUNT_MISMATCH",
                "detectedAt": "2026-09-27T02:04:16.954772Z",
            }
        )
    )
    return record.investigation.investigation_id


async def test_detection_investigation_review_and_audit(
    api: httpx.AsyncClient, database: Database
) -> None:
    """Deterministic systems detect. AI investigates. Humans authorize.

    One exception, followed from the event that recorded it to the human who
    approved the explanation, asserting the whole way that the AI proposed and
    only the human decided.
    """
    investigation_id = await given_an_exception(database)

    # 1. Detected. The financial core found a discrepancy; nothing has
    #    investigated it yet.
    assert (await api.get(f"/api/v1/investigations/{investigation_id}")).json()["status"] == "PENDING"
    recommendation = await api.get(
        f"/api/v1/investigations/{investigation_id}/recommendation"
    )
    assert recommendation.status_code == 404

    # 2. Investigated. The real agent gathers evidence through the controlled
    #    tools and proposes an explanation, which is checked against what was
    #    actually retrieved before it is stored.
    run = await api.post(f"/api/v1/investigations/{investigation_id}/run")
    assert run.status_code == 200, run.text
    body = run.json()

    assert body["status"] == "AWAITING_REVIEW"
    assert body["recommendation"]["classification"] == "PROCESSOR_FEE"
    assert body["recommendation"]["requires_human_approval"] is True
    cited = {item["reference"] for item in body["recommendation"]["evidence"]}
    assert cited == {"TX-10009", "SET-8008", "FR-14"}
    # Every citation is something a tool really returned.
    assert cited <= (
        set(body["evidence_retrieved"]["transactions"])
        | set(body["evidence_retrieved"]["settlements"])
        | set(body["evidence_retrieved"]["feeRules"])
    )

    # 3. Waiting. The AI has stopped. Nothing is resolved, and the system is
    #    holding for a person.
    detail = await api.get(f"/api/v1/investigations/{investigation_id}")
    assert detail.json()["status"] == "AWAITING_REVIEW"
    stored = (await api.get(f"/api/v1/investigations/{investigation_id}/recommendation")).json()
    assert stored["recommendation_id"].startswith("REC-")
    assert stored["confidence"] == "0.9100"
    assert "not a calibrated probability" in stored["confidence_note"]

    # 4. Authorized. A human decides, and only now does the investigation close.
    approval = await api.post(
        f"/api/v1/investigations/{investigation_id}/approve",
        json={"reviewed_by": "ops.analyst", "comment": "Fee rule matches the difference."},
    )
    assert approval.status_code == 200
    assert approval.json()["decision"] == "APPROVED"
    assert "unverified" in approval.json()["reviewer_note"]
    assert (
        (await api.get(f"/api/v1/investigations/{investigation_id}")).json()["status"] == "COMPLETED"
    )

    # 5. Accountable. The whole sequence is reconstructable, with an actor on
    #    every step.
    trail = (await api.get(f"/api/v1/investigations/{investigation_id}/audit")).json()
    assert [(item["event_type"], item["actor_type"]) for item in trail["items"]] == [
        ("INVESTIGATION_STARTED", "SYSTEM"),
        ("AI_RESULT_GENERATED", "AI"),
        ("INVESTIGATION_AWAITING_REVIEW", "SYSTEM"),
        ("REVIEW_APPROVED", "HUMAN"),
    ]
    assert trail["items"][-1]["actor_id"] == "ops.analyst"


async def test_the_completed_investigation_is_never_reopened(
    api: httpx.AsyncClient, database: Database
) -> None:
    """Approval is final in this service: it can be neither re-run nor re-decided."""
    investigation_id = await given_an_exception(database, "EX-9002")
    await api.post(f"/api/v1/investigations/{investigation_id}/run")
    await api.post(
        f"/api/v1/investigations/{investigation_id}/approve",
        json={"reviewed_by": "ops.analyst"},
    )

    assert (await api.post(f"/api/v1/investigations/{investigation_id}/run")).status_code == 409
    assert (
        (await api.post(
            f"/api/v1/investigations/{investigation_id}/approve",
            json={"reviewed_by": "someone.else"},
        )).status_code
        == 409
    )
    assert (
        (await api.get(f"/api/v1/investigations/{investigation_id}")).json()["status"] == "COMPLETED"
    )


def recording_core() -> tuple[FinancialCoreClient, list[tuple[str, str]]]:
    """A Financial Core client that records every request made through it."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/settlements"):
            payload = SETTLEMENTS
        elif path == "/api/v1/fee-rules":
            payload = FEE_RULES
        else:
            payload = TRANSACTION
        return httpx.Response(
            200, content=json.dumps(payload), headers={"content-type": "application/json"}
        )

    core = FinancialCoreClient(Settings(_env_file=None))
    core._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="http://financial-core.test", transport=httpx.MockTransport(handler)
    )
    return core, seen


async def test_nothing_in_the_lifecycle_writes_to_the_financial_core(
    app: FastAPI, api: httpx.AsyncClient, database: Database, policies: PolicySearch
) -> None:
    """The boundary that matters most, asserted on the wire.

    The transport records every request. Approving a recommendation must add
    nothing to that list, and no request made anywhere in the lifecycle may be
    anything but a GET.
    """
    core, seen = recording_core()
    agent = InvestigationAgent(investigating_model(), core, policies)
    app.state.investigation_agent = agent
    app.state.investigation_workflow = InvestigationWorkflow(
        database, agent, confidence_threshold=Decimal("0.85"), minimum_evidence=1
    )

    investigation_id = await given_an_exception(database, "EX-9003")
    await api.post(f"/api/v1/investigations/{investigation_id}/run")
    during_investigation = list(seen)

    await api.post(
        f"/api/v1/investigations/{investigation_id}/approve",
        json={"reviewed_by": "ops.analyst"},
    )

    # Approval contacted the financial core not at all.
    assert seen == during_investigation
    # And every request the investigation did make was a read.
    assert seen, "expected the investigation to read evidence"
    assert {method for method, _ in seen} == {"GET"}


async def test_an_escalated_investigation_still_stores_its_reasoning(
    app: FastAPI, api: httpx.AsyncClient, database: Database, policies: PolicySearch
) -> None:
    """Escalation is a routing decision, not a discarded investigation."""
    unsure = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            final_turn(
                classification="INSUFFICIENT_EVIDENCE",
                confidence=0.2,
                rootCause="No fee rule or policy accounts for the difference.",
                evidence=[{"sourceType": "TRANSACTION", "reference": "TX-10009"}],
                recommendedAction="Escalate for manual investigation.",
            ),
        ]
    )
    agent = InvestigationAgent(unsure, routing_core(), policies)
    app.state.investigation_workflow = InvestigationWorkflow(
        database, agent, confidence_threshold=Decimal("0.85"), minimum_evidence=1
    )

    investigation_id = await given_an_exception(database, "EX-9004")
    body = (await api.post(f"/api/v1/investigations/{investigation_id}/run")).json()

    assert body["status"] == "ESCALATED"
    assert "INSUFFICIENT_EVIDENCE" in body["guardrail_reason"]
    # The reasoning survives, so a human picking this up starts from what was found.
    stored = (await api.get(f"/api/v1/investigations/{investigation_id}/recommendation")).json()
    assert stored["classification"] == "INSUFFICIENT_EVIDENCE"
    # And it cannot be approved: the guardrail routed it away from review.
    assert (
        (await api.post(
            f"/api/v1/investigations/{investigation_id}/approve",
            json={"reviewed_by": "ops.analyst"},
        )).status_code
        == 409
    )


async def test_a_fabricated_citation_stops_the_investigation(
    app: FastAPI, api: httpx.AsyncClient, database: Database, policies: PolicySearch
) -> None:
    """The grounding guarantee, end to end.

    A model citing a fee rule no tool returned produces no stored conclusion at
    all — not a lower-confidence one, and not one with the bad citation dropped.
    """
    fabricating = FakeModel(
        [
            tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
            final_turn(
                confidence=0.99,
                evidence=[{"sourceType": "FEE_RULE", "reference": "FR-999"}],
            ),
        ]
    )
    agent = InvestigationAgent(fabricating, routing_core(), policies)
    app.state.investigation_workflow = InvestigationWorkflow(
        database, agent, confidence_threshold=Decimal("0.85"), minimum_evidence=1
    )

    investigation_id = await given_an_exception(database, "EX-9005")
    response = await api.post(f"/api/v1/investigations/{investigation_id}/run")

    assert response.status_code == 422
    assert "FR-999" in response.json()["detail"]
    assert (await api.get(f"/api/v1/investigations/{investigation_id}")).json()["status"] == "FAILED"
    missing = await api.get(f"/api/v1/investigations/{investigation_id}/recommendation")
    assert missing.status_code == 404
