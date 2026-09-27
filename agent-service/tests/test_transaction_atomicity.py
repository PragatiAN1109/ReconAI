"""Transaction boundaries, proven by rolling real transactions back.

What is under test here is not application logic but the boundary itself: that
the success path is one transaction, that a failure anywhere inside it leaves
nothing behind, and that the failure path is a separate transaction that runs
afterwards.

**No mocks and no patching.** A mock cannot demonstrate PostgreSQL rollback
semantics; it can only demonstrate that a mock was called. Failures are injected
as real ``RAISE EXCEPTION`` triggers inside the database, so the error arrives
through asyncpg exactly as a constraint violation or a disk error would.

The triggers are also what pins down *when* the failure happens. The evidence
trigger fires only when a row for that recommendation already exists, so the
fact that it fired at all proves the recommendation and the first evidence row
had already been written and were visible inside the transaction. Rolling back
from a point where nothing had been written would prove nothing.

Verification is always done from a **separate connection pool** opened after the
fact, so what is asserted is committed database state rather than anything left
in the session that failed.
"""

from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa

from app.audit_service import AuditService
from app.config import Settings
from app.database import Database
from app.investigation_service import InvestigationService
from app.investigation_workflow import InvestigationNotRunnable
from app.models import SCHEMA, ReviewDecision
from app.review_service import ReviewService
from tests.test_investigation_lifecycle import (
    StubAgent,
    build_workflow,
    event,
    result,
)

pytestmark = pytest.mark.integration


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
async def verifier(postgres_url: str) -> AsyncIterator[Database]:
    """A second, independent connection pool used only to read committed state.

    Deliberately not the pool the workflow used. Asserting through the same
    pool would still be correct, but a separate one removes any doubt that what
    is being read is committed data rather than session residue.
    """
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
async def audit(verifier: Database) -> AuditService:
    return AuditService(verifier)


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


# ---------------------------------------------------------------------------
# Injecting a real database failure
# ---------------------------------------------------------------------------


class InjectedFailure:
    """Installs a PostgreSQL trigger that raises, then removes it.

    The failure is genuine: a ``RAISE EXCEPTION`` inside the server, surfacing
    through asyncpg as a database error mid-transaction. Nothing in the
    application is patched, so the code under test is byte-for-byte the code
    that runs in production.
    """

    def __init__(self, database: Database, name: str, table: str, when: str, event: str) -> None:
        self._database = database
        self._name = name
        self._table = table
        self._when = when
        self._event = event

    async def install(self) -> None:
        async with self._database.session() as session:
            await session.execute(
                sa.text(
                    f"CREATE OR REPLACE FUNCTION {SCHEMA}.{self._name}() RETURNS trigger AS $$ "
                    "BEGIN "
                    f"  IF {self._when} THEN "
                    f"    RAISE EXCEPTION 'injected failure in {self._name}'; "
                    "  END IF; "
                    "  RETURN NEW; "
                    "END $$ LANGUAGE plpgsql"
                )
            )
            await session.execute(
                sa.text(
                    f"CREATE TRIGGER trg_{self._name} BEFORE {self._event} "
                    f"ON {SCHEMA}.{self._table} FOR EACH ROW "
                    f"EXECUTE FUNCTION {SCHEMA}.{self._name}()"
                )
            )

    async def remove(self) -> None:
        async with self._database.session() as session:
            await session.execute(
                sa.text(f"DROP TRIGGER IF EXISTS trg_{self._name} ON {SCHEMA}.{self._table}")
            )
            await session.execute(sa.text(f"DROP FUNCTION IF EXISTS {SCHEMA}.{self._name}()"))

    async def __aenter__(self) -> "InjectedFailure":
        await self.install()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.remove()


def fail_on_second_evidence_row(database: Database) -> InjectedFailure:
    """Fail the *second* evidence insert for a recommendation.

    The condition is what makes this a meaningful injection point rather than an
    arbitrary one: it can only be true if a first evidence row for the same
    recommendation is already in the table, which in turn can only be true if
    the recommendation itself was inserted first. When this trigger fires, real
    writes have demonstrably already happened inside the transaction.
    """
    return InjectedFailure(
        database,
        name="fail_on_second_evidence",
        table="recommendation_evidence",
        event="INSERT",
        when=(
            f"(SELECT count(*) FROM {SCHEMA}.recommendation_evidence "
            "WHERE recommendation_id = NEW.recommendation_id) >= 1"
        ),
    )


def fail_on_success_audit(database: Database) -> InjectedFailure:
    """Fail the success audit events, leaving the failure audit writable.

    The latest possible point inside the result transaction: recommendation,
    every evidence row and the status transition have all been written by now.
    """
    return InjectedFailure(
        database,
        name="fail_on_success_audit",
        table="audit_events",
        event="INSERT",
        when=(
            "NEW.event_type IN ('AI_RESULT_GENERATED', 'INVESTIGATION_AWAITING_REVIEW', "
            "'INVESTIGATION_ESCALATED')"
        ),
    )


def fail_on_status_transition(database: Database) -> InjectedFailure:
    """Fail the RUNNING -> AWAITING_REVIEW transition, but not -> FAILED."""
    return InjectedFailure(
        database,
        name="fail_on_status_transition",
        table="investigations",
        event="UPDATE",
        when="NEW.status = 'AWAITING_REVIEW'",
    )


def fail_on_review_insert(database: Database) -> InjectedFailure:
    return InjectedFailure(
        database,
        name="fail_on_review_insert",
        table="reviews",
        event="INSERT",
        when="true",
    )


def fail_on_review_audit(database: Database) -> InjectedFailure:
    """Fail the review audit event after the review row and status change."""
    return InjectedFailure(
        database,
        name="fail_on_review_audit",
        table="audit_events",
        event="INSERT",
        when="NEW.event_type IN ('REVIEW_APPROVED', 'REVIEW_REJECTED')",
    )


# ---------------------------------------------------------------------------
# Reading committed state from an independent pool
# ---------------------------------------------------------------------------


async def scalar(verifier: Database, query: str, **parameters) -> object:
    async with verifier.session() as session:
        return await session.scalar(sa.text(query), parameters)


async def recommendation_count(verifier: Database, investigation_id: str) -> int:
    return await scalar(
        verifier,
        f"SELECT count(*) FROM {SCHEMA}.recommendations WHERE investigation_id = :id",
        id=investigation_id,
    )


async def evidence_count(verifier: Database, investigation_id: str) -> int:
    """Evidence rows for this investigation, found without assuming a recommendation."""
    return await scalar(
        verifier,
        f"SELECT count(*) FROM {SCHEMA}.recommendation_evidence e "
        f"LEFT JOIN {SCHEMA}.recommendations r ON r.recommendation_id = e.recommendation_id "
        "WHERE r.investigation_id = :id OR r.recommendation_id IS NULL",
        id=investigation_id,
    )


async def review_count(verifier: Database, investigation_id: str) -> int:
    return await scalar(
        verifier,
        f"SELECT count(*) FROM {SCHEMA}.reviews WHERE investigation_id = :id",
        id=investigation_id,
    )


async def status_of(verifier: Database, investigation_id: str) -> str:
    return await scalar(
        verifier,
        f"SELECT status FROM {SCHEMA}.investigations WHERE investigation_id = :id",
        id=investigation_id,
    )


async def audit_types(verifier: Database, investigation_id: str) -> list[str]:
    async with verifier.session() as session:
        rows = await session.scalars(
            sa.text(
                f"SELECT event_type FROM {SCHEMA}.audit_events "
                "WHERE investigation_id = :id ORDER BY sequence_no"
            ),
            {"id": investigation_id},
        )
        return list(rows)


async def given_pending(investigations: InvestigationService, exception_id: str) -> str:
    record = await investigations.create_or_get(event(exception_id))
    return record.investigation.investigation_id


def two_pieces_of_evidence():
    return result(
        evidence=[
            {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
            {"sourceType": "TRANSACTION", "reference": "TX-10007"},
        ]
    )


# ---------------------------------------------------------------------------
# Boundary 1: the claim commits on its own
# ---------------------------------------------------------------------------


async def test_the_claim_commits_independently_of_the_result(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """TX1 survives a later rollback, which is what makes it a separate boundary.

    If claiming shared a transaction with recording, a failure at the end would
    erase the fact that the run ever started.
    """
    investigation_id = await given_pending(investigations, "EX-8001")

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    # The INVESTIGATION_STARTED event was written by TX1 and committed before
    # the agent ran. It is still here.
    assert "INVESTIGATION_STARTED" in await audit_types(verifier, investigation_id)


# ---------------------------------------------------------------------------
# Boundary 3: result persistence is one transaction
# ---------------------------------------------------------------------------


async def test_a_failure_persisting_evidence_leaves_nothing_behind(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """The headline case: recommendation in, evidence #1 in, evidence #2 fails.

    The trigger can only fire once a first evidence row exists, so reaching it
    proves the recommendation and one evidence row were already written. All of
    it must disappear.
    """
    investigation_id = await given_pending(investigations, "EX-8002")

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    assert await recommendation_count(verifier, investigation_id) == 0
    assert await evidence_count(verifier, investigation_id) == 0
    assert await status_of(verifier, investigation_id) == "FAILED"

    types = await audit_types(verifier, investigation_id)
    assert types == ["INVESTIGATION_STARTED", "INVESTIGATION_FAILED"]
    assert "AI_RESULT_GENERATED" not in types
    assert "INVESTIGATION_AWAITING_REVIEW" not in types


async def test_a_failure_persisting_the_success_audit_rolls_back_the_result(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """Failure at the last write in the transaction.

    By this point the recommendation, both evidence rows and the status
    transition have all been written. None of them may survive.
    """
    investigation_id = await given_pending(investigations, "EX-8003")

    async with fail_on_success_audit(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    assert await recommendation_count(verifier, investigation_id) == 0
    assert await evidence_count(verifier, investigation_id) == 0
    assert await status_of(verifier, investigation_id) == "FAILED"
    assert await audit_types(verifier, investigation_id) == [
        "INVESTIGATION_STARTED",
        "INVESTIGATION_FAILED",
    ]


async def test_a_failure_applying_the_status_transition_rolls_back_the_result(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """The guardrail's status change is inside the same transaction as the result."""
    investigation_id = await given_pending(investigations, "EX-8004")

    async with fail_on_status_transition(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    assert await recommendation_count(verifier, investigation_id) == 0
    assert await evidence_count(verifier, investigation_id) == 0
    assert await status_of(verifier, investigation_id) == "FAILED"


async def test_an_escalated_result_is_equally_atomic(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """Escalation is the same transaction, not a lighter path."""
    investigation_id = await given_pending(investigations, "EX-8005")
    low_confidence = result(
        confidence=0.2,
        evidence=[
            {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
            {"sourceType": "TRANSACTION", "reference": "TX-10007"},
        ],
    )

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(low_confidence)).run(investigation_id)

    assert await recommendation_count(verifier, investigation_id) == 0
    assert await evidence_count(verifier, investigation_id) == 0
    assert await status_of(verifier, investigation_id) == "FAILED"
    assert "INVESTIGATION_ESCALATED" not in await audit_types(verifier, investigation_id)


async def test_the_investigation_never_rests_in_awaiting_review_without_a_result(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """The invariant a partial write would break, stated directly.

    An investigation showing AWAITING_REVIEW with no recommendation would send a
    reviewer to look at something that does not exist.
    """
    for index, injection in enumerate(
        (fail_on_second_evidence_row, fail_on_success_audit, fail_on_status_transition)
    ):
        investigation_id = await given_pending(investigations, f"EX-806{index}")

        async with injection(database):
            with pytest.raises(Exception, match="injected failure"):
                await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                    investigation_id
                )

        status = await status_of(verifier, investigation_id)
        assert status != "AWAITING_REVIEW", injection.__name__
        assert status == "FAILED", injection.__name__
        assert await recommendation_count(verifier, investigation_id) == 0


async def test_no_recommendation_can_exist_without_its_evidence(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """Stated from the other direction: the two are never out of step.

    Across every injection point, the number of recommendations and the number
    of evidence rows move together — both present, or both absent.
    """
    investigation_id = await given_pending(investigations, "EX-8007")

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    assert (await recommendation_count(verifier, investigation_id)) == 0
    assert (await evidence_count(verifier, investigation_id)) == 0

    # And the successful path, for contrast: both present, consistent with each other.
    other = await given_pending(investigations, "EX-8008")
    await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(other)
    assert await recommendation_count(verifier, other) == 1
    assert await evidence_count(verifier, other) == 2
    assert await status_of(verifier, other) == "AWAITING_REVIEW"


# ---------------------------------------------------------------------------
# Boundary 5: the failure transition is a separate transaction
# ---------------------------------------------------------------------------


async def test_the_failure_transition_is_its_own_transaction(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """It has to be: the transaction it follows was rolled back.

    A FAILED status and a failure audit event both surviving is only possible if
    they were written by a transaction that began after the rollback.
    """
    investigation_id = await given_pending(investigations, "EX-8009")

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    assert await status_of(verifier, investigation_id) == "FAILED"
    assert (await audit_types(verifier, investigation_id)).count("INVESTIGATION_FAILED") == 1


async def test_the_failure_audit_does_not_copy_database_error_text(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """A database error's string embeds the statement and its bound parameters.

    Copying it into the audit trail would put row contents — a root cause, an
    amount — into a table whose stated guarantee is that it holds no such
    detail. The exception type is recorded; the specifics stay in the logs.
    """
    investigation_id = await given_pending(investigations, "EX-8010")

    async with fail_on_second_evidence_row(database):
        with pytest.raises(Exception, match="injected failure"):
            await build_workflow(database, StubAgent(two_pieces_of_evidence())).run(
                investigation_id
            )

    async with verifier.session() as session:
        metadata = await session.scalar(
            sa.text(
                f"SELECT metadata FROM {SCHEMA}.audit_events "
                "WHERE investigation_id = :id AND event_type = 'INVESTIGATION_FAILED'"
            ),
            {"id": investigation_id},
        )

    assert metadata["failure_type"]
    rendered = str(metadata).lower()
    for leak in ("insert into", "select ", "parameters", "sql:", "root_cause"):
        assert leak not in rendered, leak
    # The conclusion the run proposed must not be sitting in the failure record.
    assert "settlement processing fee" not in rendered


async def test_a_concurrent_duplicate_does_not_fail_the_winners_investigation(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
) -> None:
    """A redundant run must not overwrite a valid outcome with FAILED.

    ``InvestigationNotRunnable`` from the recommendation unique constraint means
    someone else already recorded a result. Treating that as a failure of *this*
    investigation would destroy their correct answer to report our own
    redundancy.
    """
    investigation_id = await given_pending(investigations, "EX-8011")
    workflow = build_workflow(database, StubAgent())
    await workflow.run(investigation_id)

    # Force the investigation back to RUNNING so the claim guard is passed and
    # the unique constraint becomes the thing that stops the second run — the
    # narrow case the defensive branch exists for.
    async with database.session() as session:
        await session.execute(
            sa.text(
                f"UPDATE {SCHEMA}.investigations SET status = 'RUNNING' "
                "WHERE investigation_id = :id"
            ),
            {"id": investigation_id},
        )

    with pytest.raises(InvestigationNotRunnable):
        await build_workflow(database, StubAgent()).run(investigation_id)

    # The first run's recommendation is untouched, and the investigation was not
    # marked FAILED on its behalf.
    assert await recommendation_count(verifier, investigation_id) == 1
    assert await status_of(verifier, investigation_id) != "FAILED"


# ---------------------------------------------------------------------------
# The human review transaction
# ---------------------------------------------------------------------------


async def given_awaiting_review(
    database: Database, investigations: InvestigationService, exception_id: str
) -> str:
    investigation_id = await given_pending(investigations, exception_id)
    await build_workflow(database, StubAgent()).run(investigation_id)
    return investigation_id


async def test_a_failure_persisting_the_review_audit_rolls_back_the_decision(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
) -> None:
    """The review row and the status change had both been written by this point.

    A human decision that is half-recorded is worse than one not recorded at
    all: the investigation would read COMPLETED with nobody accountable for it.
    """
    investigation_id = await given_awaiting_review(database, investigations, "EX-8020")

    async with fail_on_review_audit(database):
        with pytest.raises(Exception, match="injected failure"):
            await reviews.decide(
                investigation_id,
                decision=ReviewDecision.APPROVED,
                reviewed_by="ops.analyst",
            )

    assert await review_count(verifier, investigation_id) == 0
    assert await status_of(verifier, investigation_id) == "AWAITING_REVIEW"
    assert "REVIEW_APPROVED" not in await audit_types(verifier, investigation_id)


async def test_a_failure_persisting_the_review_leaves_the_status_unchanged(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
) -> None:
    """The status transition is inside the same transaction as the review row."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-8021")

    async with fail_on_review_insert(database):
        with pytest.raises(Exception, match="injected failure"):
            await reviews.decide(
                investigation_id,
                decision=ReviewDecision.REJECTED,
                reviewed_by="ops.analyst",
            )

    assert await review_count(verifier, investigation_id) == 0
    # Not ESCALATED: the rejection never happened.
    assert await status_of(verifier, investigation_id) == "AWAITING_REVIEW"


async def test_the_investigation_is_still_reviewable_after_a_failed_decision(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
) -> None:
    """A rolled-back decision must not strand the investigation.

    Because nothing partial survived, the reviewer can simply try again — and
    the retry produces exactly one decision.
    """
    investigation_id = await given_awaiting_review(database, investigations, "EX-8022")

    async with fail_on_review_audit(database):
        with pytest.raises(Exception, match="injected failure"):
            await reviews.decide(
                investigation_id,
                decision=ReviewDecision.APPROVED,
                reviewed_by="ops.analyst",
            )

    outcome = await reviews.decide(
        investigation_id, decision=ReviewDecision.APPROVED, reviewed_by="ops.analyst"
    )

    assert outcome.review.decision == "APPROVED"
    assert await review_count(verifier, investigation_id) == 1
    assert await status_of(verifier, investigation_id) == "COMPLETED"
    assert (await audit_types(verifier, investigation_id)).count("REVIEW_APPROVED") == 1


async def test_a_failed_rejection_does_not_escalate_the_investigation(
    database: Database,
    verifier: Database,
    investigations: InvestigationService,
    reviews: ReviewService,
) -> None:
    """The status must not move without the decision that justifies it."""
    investigation_id = await given_awaiting_review(database, investigations, "EX-8023")

    async with fail_on_review_audit(database):
        with pytest.raises(Exception, match="injected failure"):
            await reviews.decide(
                investigation_id,
                decision=ReviewDecision.REJECTED,
                reviewed_by="ops.analyst",
            )

    assert await status_of(verifier, investigation_id) == "AWAITING_REVIEW"
    assert await review_count(verifier, investigation_id) == 0
    types = await audit_types(verifier, investigation_id)
    assert "REVIEW_REJECTED" not in types
    assert "INVESTIGATION_ESCALATED" not in types
