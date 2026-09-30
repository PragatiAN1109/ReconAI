"""Audit events for the operational states of automatic execution.

These describe what happened to a *run*, not what an investigation concluded,
and neither is terminal. They exist because the alternative was recording a
provider outage or an exhausted budget as ``INVESTIGATION_FAILED`` — which would
put a terminal failure in the trail of an investigation that has not failed, and
which the detail page would then render as a failure.

Run against a real PostgreSQL, because what is being asserted is the trail as it
is actually stored and ordered.
"""

import pytest

from app.auto_investigation import AutoInvestigationRunner
from app.audit_service import AuditService
from app.database import Database
from app.investigation_model import InvestigationModelError
from app.investigation_service import InvestigationService
from app.investigation_workflow import RetryContext
from app.models import AuditEventType
from app.rate_limit import FixedWindowRateLimiter
# Fixtures and helpers are reused from the lifecycle suite rather than
# duplicated: the database, the per-test truncation and the stub agent are
# already correct there, and a second copy would drift.
from tests.test_investigation_lifecycle import (  # noqa: F401
    StubAgent,
    audit,
    build_workflow,
    clean,
    database,
    event,
    given_pending,
    investigations,
    status_of,
)

pytestmark = pytest.mark.integration


async def trail(audit: AuditService, investigation_id: str) -> list[tuple[str, dict | None]]:
    return [
        (entry.event_type, entry.metadata_json)
        for entry in await audit.list_for_investigation(investigation_id)
    ]


def limiter(global_limit: int = 100) -> FixedWindowRateLimiter:
    return FixedWindowRateLimiter(
        per_client_limit=100, global_limit=global_limit, window_seconds=600
    )


# ---------------------------------------------------------------------------
# A retry that is genuinely coming
# ---------------------------------------------------------------------------


async def test_a_scheduled_retry_is_recorded_with_its_counters(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    investigation_id = await given_pending(investigations, "EX-8001")
    workflow = build_workflow(
        database, StubAgent(raises=InvestigationModelError("provider unreachable"))
    )

    with pytest.raises(InvestigationModelError):
        await workflow.run(
            investigation_id,
            retry=RetryContext(attempt=1, max_attempts=2, next_delay_seconds=2.0),
        )

    events = await trail(audit, investigation_id)
    assert [name for name, _ in events] == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
        "INVESTIGATION_RETRY_SCHEDULED",
    ]
    metadata = events[-1][1]
    assert metadata["attempt"] == 1
    assert metadata["max_attempts"] == 2
    assert metadata["backoff_seconds"] == 2.0
    assert metadata["failure_category"] == "InvestigationModelError"
    # Released, not failed: PENDING is the only runnable status.
    assert await status_of(database, investigation_id) == "PENDING"


async def test_the_retry_event_carries_no_provider_error_string(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """A provider message can embed request detail. Only the class is stored."""
    investigation_id = await given_pending(investigations, "EX-8002")
    secret = "merchant MERCH-9 amount 2500.00"

    with pytest.raises(InvestigationModelError):
        await build_workflow(
            database, StubAgent(raises=InvestigationModelError(secret))
        ).run(
            investigation_id,
            retry=RetryContext(attempt=1, max_attempts=2, next_delay_seconds=2.0),
        )

    events = await trail(audit, investigation_id)
    assert "MERCH-9" not in str(events)
    assert "2500.00" not in str(events)


async def test_no_retry_event_is_written_when_no_retry_follows(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """Promising a retry that is not coming would be a false trail entry."""
    investigation_id = await given_pending(investigations, "EX-8003")

    with pytest.raises(InvestigationModelError):
        await build_workflow(
            database, StubAgent(raises=InvestigationModelError("provider unreachable"))
        ).run(
            investigation_id,
            retry=RetryContext(attempt=2, max_attempts=2, next_delay_seconds=None),
        )

    assert [name for name, _ in await trail(audit, investigation_id)] == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
    ]


async def test_a_manual_run_records_no_retry_event(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    # The HTTP endpoint supplies no retry context: one attempt, no successor.
    investigation_id = await given_pending(investigations, "EX-8004")

    with pytest.raises(InvestigationModelError):
        await build_workflow(
            database, StubAgent(raises=InvestigationModelError("provider unreachable"))
        ).run(investigation_id)

    assert "INVESTIGATION_RETRY_SCHEDULED" not in str(
        await trail(audit, investigation_id)
    )


# ---------------------------------------------------------------------------
# Automatic execution pausing
# ---------------------------------------------------------------------------


async def test_an_exhausted_budget_records_why_automatic_execution_paused(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    investigation_id = await given_pending(investigations, "EX-8005")
    budget = limiter(global_limit=1)
    budget.check(None)  # spend the only unit
    workflow = build_workflow(database, StubAgent())
    runner = AutoInvestigationRunner(
        workflow, budget, investigations, backoff_seconds=(0.0,)
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    events = await trail(audit, investigation_id)
    assert [name for name, _ in events] == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_AUTO_RUN_PAUSED",
    ]
    assert events[-1][1]["reason"] == "AI_BUDGET_EXHAUSTED"
    assert events[-1][1]["retry_after_seconds"] > 0
    # Never started, so never failed. Still runnable.
    assert await status_of(database, investigation_id) == "PENDING"


async def test_exhausted_provider_attempts_record_a_pause_not_a_failure(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    investigation_id = await given_pending(investigations, "EX-8006")
    workflow = build_workflow(
        database, StubAgent(raises=InvestigationModelError("provider unreachable"))
    )
    runner = AutoInvestigationRunner(
        workflow, limiter(), investigations, max_attempts=2, backoff_seconds=(0.0, 0.0)
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    names = [name for name, _ in await trail(audit, investigation_id)]
    assert names == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
        "INVESTIGATION_RETRY_SCHEDULED",
        "INVESTIGATION_STARTED",
        "INVESTIGATION_AUTO_RUN_PAUSED",
    ]
    assert "INVESTIGATION_FAILED" not in names
    assert await status_of(database, investigation_id) == "PENDING"


async def test_the_pause_reason_distinguishes_budget_from_provider(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """A visitor told the wrong reason would draw the wrong conclusion."""
    investigation_id = await given_pending(investigations, "EX-8007")
    workflow = build_workflow(
        database, StubAgent(raises=InvestigationModelError("provider unreachable"))
    )
    runner = AutoInvestigationRunner(
        workflow, limiter(), investigations, max_attempts=1, backoff_seconds=(0.0,)
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    events = await trail(audit, investigation_id)
    paused = next(metadata for name, metadata in events if name.endswith("AUTO_RUN_PAUSED"))
    assert paused["reason"] == "PROVIDER_UNAVAILABLE"
    assert paused["failure_category"] == "InvestigationModelError"
    assert "retry_after_seconds" not in paused


# ---------------------------------------------------------------------------
# The successful and terminal paths are unchanged
# ---------------------------------------------------------------------------


async def test_a_successful_automatic_run_records_no_operational_events(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    investigation_id = await given_pending(investigations, "EX-8008")
    runner = AutoInvestigationRunner(
        build_workflow(database, StubAgent()), limiter(), investigations
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    names = [name for name, _ in await trail(audit, investigation_id)]
    assert names == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
        "AI_RESULT_GENERATED",
        "INVESTIGATION_AWAITING_REVIEW",
    ]
    assert "INVESTIGATION_RETRY_SCHEDULED" not in names
    assert "INVESTIGATION_AUTO_RUN_PAUSED" not in names


async def test_a_malformed_result_still_records_a_terminal_failure(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """Non-retryable failures are unchanged: FAILED, with a reason."""
    from app.investigation_agent import InvestigationFailed  # noqa: PLC0415

    investigation_id = await given_pending(investigations, "EX-8009")
    runner = AutoInvestigationRunner(
        build_workflow(
            database,
            StubAgent(raises=InvestigationFailed("returned a malformed result")),
        ),
        limiter(),
        investigations,
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    names = [name for name, _ in await trail(audit, investigation_id)]
    assert names == [
        "INVESTIGATION_CREATED",
        "INVESTIGATION_STARTED",
        "INVESTIGATION_FAILED",
    ]
    assert await status_of(database, investigation_id) == "FAILED"


# ---------------------------------------------------------------------------
# Kafka redelivery must not duplicate operational events
# ---------------------------------------------------------------------------


async def test_redelivery_does_not_duplicate_the_created_event(
    investigations: InvestigationService, audit: AuditService
) -> None:
    same = event("EX-8010")
    first = await investigations.create_or_get(same)
    second = await investigations.create_or_get(same)

    assert first.investigation.investigation_id == second.investigation.investigation_id
    assert second.created is False
    assert [name for name, _ in await trail(audit, first.investigation.investigation_id)] == [
        "INVESTIGATION_CREATED"
    ]


async def test_redelivery_does_not_duplicate_a_pause_event(
    database: Database, investigations: InvestigationService, audit: AuditService
) -> None:
    """The consumer does not reschedule a redelivered event, so no second pause.

    Asserted through the runner rather than the consumer: scheduling the same
    investigation twice is what a redelivery would cause if the created-only
    guard were ever removed, and even then the pause must not stack because the
    second run finds the budget already spent... so this asserts the honest
    thing instead — one schedule, one pause.
    """
    investigation_id = await given_pending(investigations, "EX-8011")
    budget = limiter(global_limit=1)
    budget.check(None)
    runner = AutoInvestigationRunner(
        build_workflow(database, StubAgent()), budget, investigations
    )

    runner.schedule(investigation_id)
    await runner.drain(timeout=5)

    names = [name for name, _ in await trail(audit, investigation_id)]
    assert names.count("INVESTIGATION_AUTO_RUN_PAUSED") == 1
