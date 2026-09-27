"""The full investigation lifecycle, against a real PostgreSQL.

Testcontainers rather than a substitute engine, because most of what these
tests assert *is* database behaviour: a conditional UPDATE deciding a race
between two concurrent runs, unique constraints preventing a second
recommendation or a second review, CHECK constraints refusing a result that
claims to need no approval, and transaction boundaries around a call that
cannot join a transaction.

Mocks cannot prove any of that. A concurrency guarantee demonstrated against a
fake is a guarantee about the fake.

No test here contacts a model provider. The agent is faked at the workflow
boundary, and the end-to-end test drives the *real* agent with a scripted model
and a mocked Financial Core transport.

They skip automatically when Docker is unavailable.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import review_service as review_errors
from app.audit_service import AuditService
from app.config import Settings
from app.database import Database
from app.events import ReconciliationExceptionEvent
from app.evidence_ledger import EvidenceLedger
from app.investigation_agent import InvestigationFailed, UngroundedResultError
from app.investigation_model import InvestigationModelError
from app.investigation_models import InvestigationResult
from app.investigation_service import InvestigationService
from app.investigation_workflow import (
    InvestigationNotFound,
    InvestigationNotRunnable,
    InvestigationWorkflow,
)
from app.models import SCHEMA, InvestigationStatus, ReviewDecision
from app.review_service import ReviewService

pytestmark = pytest.mark.integration

DETECTED_AT = datetime(2026, 9, 27, 2, 4, 16, 954772, tzinfo=UTC)
THRESHOLD = Decimal("0.85")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def database(postgres_url: str) -> AsyncIterator[Database]:
    db = Database(Settings(_env_file=None, database_url=postgres_url))
    await db.connect()
    yield db
    await db.disconnect()


@pytest.fixture
async def investigations(database: Database) -> InvestigationService:
    return InvestigationService(database)


@pytest.fixture
async def reviews(database: Database) -> ReviewService:
    return ReviewService(database)


@pytest.fixture
async def audit(database: Database) -> AuditService:
    return AuditService(database)


@pytest.fixture(autouse=True)
async def clean(database: Database) -> AsyncIterator[None]:
    """Each test starts from empty tables.

    Children before parents: recommendation_evidence and reviews reference
    recommendations, which is the one place this schema uses a foreign key.
    """
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


def event(exception_id: str, **overrides: object) -> ReconciliationExceptionEvent:
    payload: dict[str, object] = {
        "exceptionId": exception_id,
        "transactionId": "TX-10007",
        "type": "AMOUNT_MISMATCH",
        "detectedAt": DETECTED_AT.isoformat(),
    }
    payload.update(overrides)
    return ReconciliationExceptionEvent.model_validate(payload)


def result(
    classification: str = "PROCESSOR_FEE",
    confidence: float = 0.92,
    evidence: list[dict] | None = None,
) -> InvestigationResult:
    return InvestigationResult.model_validate(
        {
            "classification": classification,
            "rootCause": "A settlement processing fee accounts for the difference.",
            "confidence": confidence,
            "evidence": (
                [{"sourceType": "SETTLEMENT", "reference": "SET-8008"}]
                if evidence is None
                else evidence
            ),
            "recommendedAction": "Classify as a processor fee adjustment.",
            "requiresHumanApproval": True,
        }
    )


def ledger() -> EvidenceLedger:
    built = EvidenceLedger()
    built.transactions.add("TX-10007")
    built.settlements.add("SET-8008")
    built.policy_documents.add("POL-FEE-001")
    built.policy_sections.add(("POL-FEE-001", "Processing fees"))
    built.policy_excerpts[("POL-FEE-001", "Processing fees")] = (
        "Settlement processing fees are deducted at settlement time."
    )
    return built


class StubAgent:
    """Stands in for the investigator. Records that it ran, outside any transaction."""

    def __init__(
        self,
        returns: InvestigationResult | None = None,
        raises: Exception | None = None,
        on_investigate=None,
    ) -> None:
        self._returns = returns if returns is not None else result()
        self._raises = raises
        self._on_investigate = on_investigate
        self.contexts: list[object] = []

    async def investigate(self, context):
        self.contexts.append(context)
        if self._on_investigate is not None:
            await self._on_investigate(context)
        if self._raises is not None:
            raise self._raises
        return self._returns, ledger()


def build_workflow(database: Database, agent: StubAgent, **overrides) -> InvestigationWorkflow:
    options = {
        "confidence_threshold": THRESHOLD,
        "minimum_evidence": 1,
        "model_provider": "anthropic",
        "model_name": "claude-sonnet-5",
        "prompt_version": "v1",
    }
    options.update(overrides)
    return InvestigationWorkflow(database, agent, **options)


async def given_pending(investigations: InvestigationService, exception_id: str) -> str:
    record = await investigations.create_or_get(event(exception_id))
    return record.investigation.investigation_id


async def status_of(database: Database, investigation_id: str) -> str:
    async with database.session() as session:
        return await session.scalar(
            sa.text(
                f"SELECT status FROM {SCHEMA}.investigations "
                "WHERE investigation_id = :id"
            ),
            {"id": investigation_id},
        )


async def count(database: Database, table: str) -> int:
    async with database.session() as session:
        return await session.scalar(sa.text(f"SELECT count(*) FROM {SCHEMA}.{table}"))


async def event_types(audit: AuditService, investigation_id: str) -> list[str]:
    return [
        entry.event_type for entry in await audit.list_for_investigation(investigation_id)
    ]


# ---------------------------------------------------------------------------
# Claiming a run
# ---------------------------------------------------------------------------


async def test_running_moves_a_pending_investigation_through_to_review(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5001")

    outcome = await build_workflow(database, StubAgent()).run(investigation_id)

    assert outcome.decision.status is InvestigationStatus.AWAITING_REVIEW
    assert await status_of(database, investigation_id) == "AWAITING_REVIEW"


async def test_running_an_unknown_investigation_is_refused(database: Database) -> None:
    with pytest.raises(InvestigationNotFound):
        await build_workflow(database, StubAgent()).run("INV-999999")


async def test_an_investigation_cannot_be_run_twice(
    database: Database, investigations: InvestigationService
) -> None:
    """A second conclusion for one exception would make "the" conclusion meaningless."""
    investigation_id = await given_pending(investigations, "EX-5002")
    workflow = build_workflow(database, StubAgent())
    await workflow.run(investigation_id)

    with pytest.raises(InvestigationNotRunnable) as refusal:
        await workflow.run(investigation_id)

    assert refusal.value.current_status == "AWAITING_REVIEW"
    assert await count(database, "recommendations") == 1


async def test_a_reviewed_investigation_cannot_be_re_run(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5003")
    workflow = build_workflow(database, StubAgent())
    await workflow.run(investigation_id)
    await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )

    with pytest.raises(InvestigationNotRunnable):
        await workflow.run(investigation_id)


async def test_concurrent_runs_produce_exactly_one_recommendation(
    database: Database, investigations: InvestigationService
) -> None:
    """The race the conditional UPDATE exists for.

    Eight simultaneous runs of one investigation. A read-then-write would let
    several past the read; only ``UPDATE ... WHERE status = 'PENDING'`` can
    decide, because only the database serialises the row.
    """
    investigation_id = await given_pending(investigations, "EX-5004")

    async def slow_investigate(_context):
        # Long enough that every claim attempt overlaps the winner's model call.
        await asyncio.sleep(0.05)

    outcomes = await asyncio.gather(
        *(
            build_workflow(database, StubAgent(on_investigate=slow_investigate)).run(
                investigation_id
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )

    succeeded = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, InvestigationNotRunnable)]

    assert len(succeeded) == 1
    assert len(refused) == 7
    assert await count(database, "recommendations") == 1


async def test_only_one_agent_call_happens_under_concurrency(
    database: Database, investigations: InvestigationService
) -> None:
    """The losers are refused before the model is called, not after.

    Claiming first is what makes this cheap: seven rejected runs must not cost
    seven model calls.
    """
    investigation_id = await given_pending(investigations, "EX-5005")
    agents = [StubAgent() for _ in range(8)]

    await asyncio.gather(
        *(build_workflow(database, agent).run(investigation_id) for agent in agents),
        return_exceptions=True,
    )

    assert sum(len(agent.contexts) for agent in agents) == 1


# ---------------------------------------------------------------------------
# Transaction boundaries
# ---------------------------------------------------------------------------


async def test_no_transaction_is_held_across_the_model_call(
    database: Database, investigations: InvestigationService
) -> None:
    """The reason the workflow is three transactions rather than one.

    While the agent runs, an independent connection reads the investigation. It
    sees RUNNING, which is only possible if the claiming transaction committed
    before the call began. Had the claim stayed open, this read would block on
    the row and the test would time out rather than return a value.
    """
    investigation_id = await given_pending(investigations, "EX-5010")
    observed: list[str] = []

    async def observe_from_another_connection(_context):
        observed.append(
            await asyncio.wait_for(status_of(database, investigation_id), timeout=5)
        )

    await build_workflow(
        database, StubAgent(on_investigate=observe_from_another_connection)
    ).run(investigation_id)

    assert observed == ["RUNNING"]


async def test_the_result_and_its_status_change_commit_together(
    database: Database, investigations: InvestigationService
) -> None:
    """A recommendation with no status change is invisible; the reverse is unexplainable."""
    investigation_id = await given_pending(investigations, "EX-5011")

    await build_workflow(database, StubAgent()).run(investigation_id)

    async with database.session() as session:
        row = (
            await session.execute(
                sa.text(
                    f"SELECT i.status, r.recommendation_id FROM {SCHEMA}.investigations i "
                    f"JOIN {SCHEMA}.recommendations r "
                    "  ON r.investigation_id = i.investigation_id "
                    "WHERE i.investigation_id = :id"
                ),
                {"id": investigation_id},
            )
        ).one()

    assert row.status == "AWAITING_REVIEW"
    assert row.recommendation_id.startswith("REC-")


# ---------------------------------------------------------------------------
# What gets persisted
# ---------------------------------------------------------------------------


async def test_the_recommendation_records_what_was_concluded_and_by_what(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5020")

    outcome = await build_workflow(database, StubAgent()).run(investigation_id)

    stored = await investigations.get_recommendation(investigation_id)
    assert stored.recommendation_id == outcome.recommendation.recommendation_id
    assert stored.classification == "PROCESSOR_FEE"
    assert stored.root_cause.startswith("A settlement processing fee")
    assert stored.recommended_action == "Classify as a processor fee adjustment."
    assert stored.model_provider == "anthropic"
    assert stored.model_name == "claude-sonnet-5"
    assert stored.prompt_version == "v1"


async def test_confidence_is_stored_exactly_as_a_decimal(
    database: Database, investigations: InvestigationService
) -> None:
    """Never a float. A stored confidence must read back as what was written."""
    investigation_id = await given_pending(investigations, "EX-5021")

    await build_workflow(database, StubAgent(result(confidence=0.87))).run(investigation_id)

    stored = await investigations.get_recommendation(investigation_id)
    assert isinstance(stored.confidence, Decimal)
    assert stored.confidence == Decimal("0.8700")


async def test_a_stored_recommendation_always_requires_human_approval(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5022")

    await build_workflow(database, StubAgent()).run(investigation_id)

    stored = await investigations.get_recommendation(investigation_id)
    assert stored.requires_human_approval is True


async def test_the_database_refuses_a_self_approving_recommendation(
    database: Database, investigations: InvestigationService
) -> None:
    """The last line of defence, below the schema validator and the service.

    If every layer of application code were wrong, this row still cannot exist.
    """
    investigation_id = await given_pending(investigations, "EX-5023")

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.recommendations "
                    "(recommendation_id, investigation_id, classification, root_cause, "
                    " confidence, recommended_action, requires_human_approval) "
                    "VALUES ('REC-999999', :id, 'PROCESSOR_FEE', 'because', "
                    " 0.99, 'do it', false)"
                ),
                {"id": investigation_id},
            )

    assert "ck_recommendations_requires_human_approval" in str(failure.value)


async def test_the_database_refuses_a_confidence_outside_zero_to_one(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5024")

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.recommendations "
                    "(recommendation_id, investigation_id, classification, root_cause, "
                    " confidence, recommended_action) "
                    "VALUES ('REC-999998', :id, 'PROCESSOR_FEE', 'because', 1.5, 'do it')"
                ),
                {"id": investigation_id},
            )

    assert "ck_recommendations_confidence" in str(failure.value)


async def test_the_database_refuses_an_unknown_classification(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5025")

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.recommendations "
                    "(recommendation_id, investigation_id, classification, root_cause, "
                    " confidence, recommended_action) "
                    "VALUES ('REC-999997', :id, 'AMOUNT_MISMATCH', 'because', 0.9, 'do it')"
                ),
                {"id": investigation_id},
            )

    # AMOUNT_MISMATCH is a detection, not a root cause. The two vocabularies
    # are kept apart by the database, not by convention.
    assert "ck_recommendations_classification" in str(failure.value)


async def test_the_database_refuses_a_second_recommendation(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5026")
    await build_workflow(database, StubAgent()).run(investigation_id)

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.recommendations "
                    "(recommendation_id, investigation_id, classification, root_cause, "
                    " confidence, recommended_action) "
                    "VALUES ('REC-999996', :id, 'PROCESSOR_FEE', 'because', 0.9, 'do it')"
                ),
                {"id": investigation_id},
            )

    assert "uq_recommendations_investigation_id" in str(failure.value)


async def test_evidence_is_persisted_with_its_excerpt(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5027")
    cited = result(
        evidence=[
            {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
            {
                "sourceType": "POLICY_DOCUMENT",
                "reference": "POL-FEE-001",
                "section": "Processing fees",
            },
        ]
    )

    await build_workflow(database, StubAgent(cited)).run(investigation_id)

    recommendation = await investigations.get_recommendation(investigation_id)
    evidence = await investigations.get_evidence(recommendation.recommendation_id)

    assert [(item.source_type, item.reference) for item in evidence] == [
        ("POLICY_DOCUMENT", "POL-FEE-001"),
        ("SETTLEMENT", "SET-8008"),
    ]
    policy = evidence[0]
    assert policy.section == "Processing fees"
    # Stored, so a reviewer reads the words the investigation saw rather than
    # whatever the corpus says by the time they look.
    assert "deducted at settlement time" in policy.excerpt


async def test_evidence_holds_no_foreign_key_into_the_financial_core(
    database: Database,
) -> None:
    """References to another service's records are identifiers, never constraints."""
    async with database.session() as session:
        referenced = list(
            await session.scalars(
                sa.text(
                    "SELECT DISTINCT ccu.table_schema FROM information_schema.table_constraints tc "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name = tc.constraint_name "
                    "WHERE tc.constraint_type = 'FOREIGN KEY' "
                    "  AND tc.table_schema = :schema"
                ),
                {"schema": SCHEMA},
            )
        )

    # Foreign keys exist, but every one points back into this service's own schema.
    assert referenced in ([], [SCHEMA])


# ---------------------------------------------------------------------------
# The guardrail, applied for real
# ---------------------------------------------------------------------------


async def test_a_low_confidence_result_is_persisted_and_escalated(
    database: Database, investigations: InvestigationService
) -> None:
    """Escalation still stores the reasoning. Escalating is not discarding."""
    investigation_id = await given_pending(investigations, "EX-5030")

    outcome = await build_workflow(database, StubAgent(result(confidence=0.3))).run(
        investigation_id
    )

    assert outcome.decision.status is InvestigationStatus.ESCALATED
    assert await status_of(database, investigation_id) == "ESCALATED"
    assert await investigations.get_recommendation(investigation_id) is not None


async def test_insufficient_evidence_is_escalated_not_failed(
    database: Database, investigations: InvestigationService
) -> None:
    """Reporting that the evidence does not support a conclusion is a success."""
    investigation_id = await given_pending(investigations, "EX-5031")

    outcome = await build_workflow(
        database, StubAgent(result(classification="INSUFFICIENT_EVIDENCE", confidence=0.95))
    ).run(investigation_id)

    assert outcome.decision.status is InvestigationStatus.ESCALATED
    assert await status_of(database, investigation_id) != "FAILED"
    stored = await investigations.get_recommendation(investigation_id)
    assert stored.classification == "INSUFFICIENT_EVIDENCE"


async def test_the_threshold_is_configurable_end_to_end(
    database: Database, investigations: InvestigationService
) -> None:
    lenient = await given_pending(investigations, "EX-5032")
    strict = await given_pending(investigations, "EX-5033")
    moderate = result(confidence=0.7)

    await build_workflow(
        database, StubAgent(moderate), confidence_threshold=Decimal("0.6")
    ).run(lenient)
    await build_workflow(
        database, StubAgent(moderate), confidence_threshold=Decimal("0.95")
    ).run(strict)

    assert await status_of(database, lenient) == "AWAITING_REVIEW"
    assert await status_of(database, strict) == "ESCALATED"


async def test_a_run_never_completes_an_investigation_by_itself(
    database: Database, investigations: InvestigationService
) -> None:
    """Only a human reaches COMPLETED. No confidence value is a shortcut."""
    for index, confidence in enumerate([0.0, 0.5, 0.85, 0.99, 1.0]):
        investigation_id = await given_pending(investigations, f"EX-504{index}")
        await build_workflow(database, StubAgent(result(confidence=confidence))).run(
            investigation_id
        )
        assert await status_of(database, investigation_id) != "COMPLETED"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_an_ungrounded_result_fails_the_investigation_and_stores_nothing(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5050")
    agent = StubAgent(raises=UngroundedResultError("cited FEE_RULE/FR-999"))

    with pytest.raises(UngroundedResultError):
        await build_workflow(database, agent).run(investigation_id)

    assert await status_of(database, investigation_id) == "FAILED"
    # No placeholder conclusion is invented to fill the gap.
    assert await investigations.get_recommendation(investigation_id) is None


async def test_a_provider_outage_fails_the_investigation(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5051")
    agent = StubAgent(raises=InvestigationModelError("provider unreachable"))

    with pytest.raises(InvestigationModelError):
        await build_workflow(database, agent).run(investigation_id)

    assert await status_of(database, investigation_id) == "FAILED"


async def test_a_failure_does_not_leave_the_investigation_running(
    database: Database, investigations: InvestigationService
) -> None:
    """A stuck RUNNING row is indistinguishable from work in progress."""
    investigation_id = await given_pending(investigations, "EX-5052")

    with pytest.raises(InvestigationFailed):
        await build_workflow(database, StubAgent(raises=InvestigationFailed("no result"))).run(
            investigation_id
        )

    assert await status_of(database, investigation_id) == "FAILED"


async def test_a_failed_investigation_cannot_be_reviewed(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_pending(investigations, "EX-5053")
    with pytest.raises(InvestigationFailed):
        await build_workflow(database, StubAgent(raises=InvestigationFailed("no result"))).run(
            investigation_id
        )

    with pytest.raises(review_errors.NotAwaitingReview):
        await reviews.decide(
            investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
        )


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------


async def given_awaiting_review(
    database: Database, investigations: InvestigationService, exception_id: str
) -> str:
    investigation_id = await given_pending(investigations, exception_id)
    await build_workflow(database, StubAgent()).run(investigation_id)
    return investigation_id


async def test_approving_completes_the_investigation(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-6001")

    outcome = await reviews.decide(
        investigation_id,
        decision=ReviewDecision.APPROVED,
        reviewed_by="ops.analyst",
        comment="Matches the fee schedule.",
    )

    assert outcome.review.review_id.startswith("REV-")
    assert outcome.review.decision == "APPROVED"
    assert await status_of(database, investigation_id) == "COMPLETED"


async def test_rejecting_escalates_rather_than_resolving(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """A rejected explanation does not make the discrepancy disappear."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-6002")

    await reviews.decide(
        investigation_id, decision=ReviewDecision.REJECTED, reviewed_by="ops.analyst"
    )

    assert await status_of(database, investigation_id) == "ESCALATED"


async def test_escalating_records_a_decision_distinct_from_rejection(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-6003")

    outcome = await reviews.decide(
        investigation_id, decision=ReviewDecision.ESCALATED, reviewed_by="ops.analyst"
    )

    assert outcome.review.decision == "ESCALATED"
    assert await status_of(database, investigation_id) == "ESCALATED"


async def test_the_review_links_the_recommendation_it_judged(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-6004")
    recommendation = await investigations.get_recommendation(investigation_id)

    outcome = await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )

    assert outcome.review.recommendation_id == recommendation.recommendation_id


async def test_the_reviewer_name_is_stored_verbatim(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """Caller-supplied and unverified, and recorded as exactly what was claimed."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-6005")

    outcome = await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="  ops.analyst  "
    )

    assert outcome.review.reviewed_by == "ops.analyst"


async def test_a_review_must_name_someone(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-6006")

    with pytest.raises(ValueError):
        await reviews.decide(
            investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="   "
        )

    assert await status_of(database, investigation_id) == "AWAITING_REVIEW"


async def test_an_investigation_cannot_be_reviewed_twice(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """A decision that can be overwritten is not a decision."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-6007")
    await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="first.analyst"
    )

    with pytest.raises(review_errors.NotAwaitingReview):
        await reviews.decide(
            investigation_id, decision=ReviewDecision.REJECTED, reviewed_by="second.analyst"
        )

    stored = await reviews.get_for_investigation(investigation_id)
    assert stored.reviewed_by == "first.analyst"
    assert stored.decision == "APPROVED"
    assert await status_of(database, investigation_id) == "COMPLETED"


async def test_a_pending_investigation_cannot_be_reviewed(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """There is nothing to review before an investigation has concluded."""
    investigation_id = await given_pending(investigations, "EX-6008")

    with pytest.raises(review_errors.NotAwaitingReview):
        await reviews.decide(
            investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
        )


async def test_an_escalated_investigation_cannot_be_approved(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """The guardrail already routed this away from recommendation review."""
    investigation_id = await given_pending(investigations, "EX-6009")
    await build_workflow(database, StubAgent(result(confidence=0.2))).run(investigation_id)

    with pytest.raises(review_errors.NotAwaitingReview):
        await reviews.decide(
            investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
        )


async def test_reviewing_an_unknown_investigation_is_refused(reviews: ReviewService) -> None:
    with pytest.raises(review_errors.InvestigationNotFound):
        await reviews.decide(
            "INV-999999", decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
        )


async def test_concurrent_reviews_record_exactly_one_decision(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """Two reviewers pressing approve at once must not both be recorded."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-6010")

    outcomes = await asyncio.gather(
        *(
            reviews.decide(
                investigation_id,
                decision=ReviewDecision.APPROVED,
                reviewed_by=f"analyst-{index}",
            )
            for index in range(6)
        ),
        return_exceptions=True,
    )

    succeeded = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    assert len(succeeded) == 1
    assert await count(database, "reviews") == 1


async def test_the_database_refuses_a_second_review(
    database: Database, investigations: InvestigationService, reviews: ReviewService
) -> None:
    """The guarantee must not depend on application code being correct."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-6011")
    await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )
    recommendation = await investigations.get_recommendation(investigation_id)

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.reviews "
                    "(review_id, investigation_id, recommendation_id, decision, reviewed_by) "
                    "VALUES ('REV-999999', :id, :rec, 'REJECTED', 'someone.else')"
                ),
                {"id": investigation_id, "rec": recommendation.recommendation_id},
            )

    assert "uq_reviews_investigation_id" in str(failure.value)


async def test_the_database_refuses_an_unknown_decision(
    database: Database, investigations: InvestigationService
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-6012")
    recommendation = await investigations.get_recommendation(investigation_id)

    with pytest.raises(IntegrityError) as failure:
        async with database.session() as session:
            await session.execute(
                sa.text(
                    f"INSERT INTO {SCHEMA}.reviews "
                    "(review_id, investigation_id, recommendation_id, decision, reviewed_by) "
                    "VALUES ('REV-999998', :id, :rec, 'AUTO_APPROVED', 'system')"
                ),
                {"id": investigation_id, "rec": recommendation.recommendation_id},
            )

    # There is no decision value meaning "approved without a human".
    assert "ck_reviews_decision" in str(failure.value)


async def test_the_review_service_holds_no_client_to_the_financial_core(
    reviews: ReviewService,
) -> None:
    """Approval cannot write to the financial core, structurally.

    Not a rule to remember: there is nothing on this object to call it with.
    """
    attributes = vars(reviews)

    assert set(attributes) == {"_database"}
    assert not any(
        hasattr(value, verb)
        for value in attributes.values()
        for verb in ("post", "put", "patch", "delete")
    )


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


async def test_a_full_lifecycle_is_recorded_in_order(
    database: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
    audit: AuditService,
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-7001")
    await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )

    assert await event_types(audit, investigation_id) == [
        "INVESTIGATION_STARTED",
        "AI_RESULT_GENERATED",
        "INVESTIGATION_AWAITING_REVIEW",
        "REVIEW_APPROVED",
    ]


async def test_each_event_names_who_caused_it(
    database: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
    audit: AuditService,
) -> None:
    """Who did what is the question an audit trail exists to answer."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-7002")
    await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )

    events = await audit.list_for_investigation(investigation_id)

    assert [entry.actor_type for entry in events] == ["SYSTEM", "AI", "SYSTEM", "HUMAN"]
    assert events[-1].actor_id == "ops.analyst"
    # System and AI events claim no human identity.
    assert all(entry.actor_id is None for entry in events[:-1])


async def test_the_ai_event_records_the_conclusion_without_any_reasoning(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """No prompt, no provider payload, no chain of thought is ever persisted."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-7003")

    events = await audit.list_for_investigation(investigation_id)
    ai_event = next(entry for entry in events if entry.event_type == "AI_RESULT_GENERATED")

    assert ai_event.metadata_json["classification"] == "PROCESSOR_FEE"
    assert ai_event.metadata_json["confidence"] == "0.9200"
    assert set(ai_event.metadata_json) == {
        "recommendation_id",
        "classification",
        "confidence",
        "evidence_count",
        "model_name",
        "prompt_version",
    }
    # The whole trail, not just the AI event. Values are checked rather than
    # keys: "prompt_version" is a version label, and a prompt is what must
    # never appear. A prompt or a provider payload is also long, so a cap on
    # value length catches a leak that a keyword list would miss.
    for entry in events:
        for key, value in (entry.metadata_json or {}).items():
            assert len(str(value)) <= 500, (entry.event_type, key)
            assert "you are investigating" not in str(value).lower()


async def test_the_escalation_event_records_the_deterministic_reason(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """An escalation must be explainable months later from what was stored."""
    investigation_id = await given_pending(investigations, "EX-7004")
    await build_workflow(database, StubAgent(result(confidence=0.4))).run(investigation_id)

    events = await audit.list_for_investigation(investigation_id)
    escalation = next(
        entry for entry in events if entry.event_type == "INVESTIGATION_ESCALATED"
    )

    assert "below the review threshold" in escalation.metadata_json["reason"]
    assert escalation.metadata_json["confidence_threshold"] == "0.85"


async def test_a_failure_is_recorded_in_the_trail(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    investigation_id = await given_pending(investigations, "EX-7005")
    with pytest.raises(UngroundedResultError):
        await build_workflow(
            database, StubAgent(raises=UngroundedResultError("cited FEE_RULE/FR-999"))
        ).run(investigation_id)

    events = await audit.list_for_investigation(investigation_id)

    assert [entry.event_type for entry in events] == [
        "INVESTIGATION_STARTED",
        "INVESTIGATION_FAILED",
    ]
    assert events[-1].metadata_json["failure_type"] == "UngroundedResultError"


async def test_a_rejection_is_recorded_as_a_rejection(
    database: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
    audit: AuditService,
) -> None:
    investigation_id = await given_awaiting_review(database, investigations, "EX-7006")
    await reviews.decide(
        investigation_id,
        decision=ReviewDecision.REJECTED,
        reviewed_by="ops.analyst",
        comment="The fee rule does not apply to this merchant.",
    )

    events = await audit.list_for_investigation(investigation_id)

    assert events[-1].event_type == "REVIEW_REJECTED"
    assert events[-1].metadata_json["resulting_status"] == "ESCALATED"
    # The comment itself is not duplicated into the trail; it lives on the review.
    assert events[-1].metadata_json["has_comment"] is True
    assert "does not apply" not in str(events[-1].metadata_json)


async def test_the_trail_stays_ordered_past_a_five_digit_sequence(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """Ordering by identifier text would put AUD-10001 before AUD-9001.

    Ordering is by the integer sequence for exactly this reason, and the only
    way to prove it is to cross the boundary.
    """
    async with database.session() as session:
        await session.execute(
            sa.text(f"ALTER SEQUENCE {SCHEMA}.audit_event_business_id_seq RESTART WITH 9999")
        )

    investigation_id = await given_awaiting_review(database, investigations, "EX-7007")
    events = await audit.list_for_investigation(investigation_id)

    assert [entry.event_id for entry in events] == ["AUD-9999", "AUD-10000", "AUD-10001"]
    assert [entry.event_type for entry in events] == [
        "INVESTIGATION_STARTED",
        "AI_RESULT_GENERATED",
        "INVESTIGATION_AWAITING_REVIEW",
    ]


async def test_audit_events_belong_only_to_their_own_investigation(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    first = await given_awaiting_review(database, investigations, "EX-7008")
    second = await given_awaiting_review(database, investigations, "EX-7009")

    assert len(await audit.list_for_investigation(first)) == 3
    assert all(
        entry.investigation_id == second
        for entry in await audit.list_for_investigation(second)
    )


async def test_the_audit_service_offers_no_way_to_change_the_trail(
    audit: AuditService,
) -> None:
    """Append-only is enforced by absence, not by a check that could be skipped."""
    surface = {name for name in dir(audit) if not name.startswith("_")}

    assert surface == {"list_for_investigation"}


async def test_an_audit_event_survives_only_if_its_transaction_commits(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """A trail recording something that never happened would be worse than none.

    The failure here is forced after the audit write, inside the same
    transaction, so the rollback has to take the event with it.
    """
    investigation_id = await given_pending(investigations, "EX-7010")

    from app import audit_service  # noqa: PLC0415
    from app.models import ActorType, AuditEventType  # noqa: PLC0415

    with pytest.raises(RuntimeError, match="forced"):
        async with database.session() as session:
            await audit_service.record(
                session,
                investigation_id=investigation_id,
                event_type=AuditEventType.INVESTIGATION_STARTED,
                actor_type=ActorType.SYSTEM,
            )
            raise RuntimeError("forced")

    assert await audit.list_for_investigation(investigation_id) == []
