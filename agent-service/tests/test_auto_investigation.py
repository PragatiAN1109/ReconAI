"""Automatic, event-driven investigation.

What these protect, in order of how much it would cost to get wrong:

1. **Duplicate Kafka delivery must not cause duplicate Anthropic spend.**
2. **Provider latency must not block Kafka acknowledgement**, or one slow
   investigation stalls the topic.
3. **An investigation failure must not kill the consumer.**
4. Budget exhaustion must leave the investigation runnable, not failed.

None of this relaxes the investigation itself. The workflow under test here is a
stub; the real one's validation, grounding and guardrail are covered by their own
suites and are unchanged.
"""

import asyncio
import json

import pytest

from app.auto_investigation import AutoInvestigationRunner
from app.config import Settings
from app.investigation_model import InvestigationModelError
from app.investigation_service import InvestigationRecord
from app.investigation_workflow import InvestigationNotFound, InvestigationNotRunnable
from app.kafka_consumer import ReconciliationExceptionConsumer
from app.message_handler import RecordLocation
from app.processing import ProcessingOutcome
from app.rate_limit import FixedWindowRateLimiter

LOCATION = RecordLocation(topic="reconciliation.exceptions", partition=0, offset=7)


def event_bytes(exception_id: str = "EX-2001") -> bytes:
    return json.dumps(
        {
            "exceptionId": exception_id,
            "transactionId": "TX-10043",
            "type": "AMOUNT_MISMATCH",
            "detectedAt": "2026-09-30T10:00:00Z",
        }
    ).encode()


class StubInvestigation:
    def __init__(self, investigation_id: str, exception_id: str, status: str) -> None:
        self.investigation_id = investigation_id
        self.exception_id = exception_id
        self.status = status


class StubInvestigationService:
    """Records events, and reports whether each was newly created."""

    def __init__(self, *, created: bool = True, raises: Exception | None = None) -> None:
        self._created = created
        self._raises = raises
        self.calls: list[str] = []

    async def create_or_get(self, event) -> InvestigationRecord:
        self.calls.append(event.exception_id)
        if self._raises is not None:
            raise self._raises
        return InvestigationRecord(
            investigation=StubInvestigation("INV-2001", event.exception_id, "PENDING"),
            created=self._created,
        )


class StubWorkflow:
    """Stands in for the real workflow. Counts provider runs."""

    def __init__(self, *, raises: Exception | None = None, delay: float = 0.0) -> None:
        self._raises = raises
        self._delay = delay
        self.runs: list[str] = []
        #: The RetryContext each call received, so the audit contract can be
        #: asserted: the workflow records a scheduled retry only when one is
        #: genuinely coming, and it needs this to know.
        self.retry_contexts: list[object] = []
        self.concurrent = 0
        self.peak_concurrent = 0

    async def run(self, investigation_id: str, *, retry=None):
        self.runs.append(investigation_id)
        self.retry_contexts.append(retry)
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._raises is not None:
                raise self._raises
            return _outcome("AWAITING_REVIEW")
        finally:
            self.concurrent -= 1


def _outcome(status: str):
    class Decision:
        def __init__(self) -> None:
            self.status = type("Status", (), {"value": status})()

    return type("Outcome", (), {"decision": Decision()})()


def limiter(*, global_limit: int = 100, per_client: int = 100) -> FixedWindowRateLimiter:
    return FixedWindowRateLimiter(
        per_client_limit=per_client, global_limit=global_limit, window_seconds=600
    )


def runner(workflow, budget=None, **kwargs) -> AutoInvestigationRunner:
    return AutoInvestigationRunner(
        workflow, budget or limiter(), backoff_seconds=(0.0, 0.0), **kwargs
    )


def settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        service_name="reconai-investigation-service",
        environment="test",
        host="127.0.0.1",
        port=8000,
        log_level="INFO",
        **overrides,
    )


def consumer(investigations, run=None) -> ReconciliationExceptionConsumer:
    return ReconciliationExceptionConsumer(settings(), investigations, runner=run)


# ---------------------------------------------------------------------------
# The consumer schedules, and never waits
# ---------------------------------------------------------------------------


class TestScheduling:
    async def test_a_new_investigation_is_scheduled(self) -> None:
        workflow = StubWorkflow()
        run = runner(workflow)

        outcome = await consumer(StubInvestigationService(), run).process_record(
            event_bytes(), LOCATION
        )

        assert outcome is ProcessingOutcome.PROCESSED
        await run.drain(timeout=1)
        assert workflow.runs == ["INV-2001"]

    async def test_the_offset_is_committed_without_waiting_for_the_provider(self) -> None:
        """The property that keeps the topic moving.

        process_record must return — which is what lets the loop commit — while
        the investigation is still running. If this ever becomes sequential, one
        slow model call stalls every subsequent exception.
        """
        workflow = StubWorkflow(delay=5.0)
        run = runner(workflow)

        outcome = await asyncio.wait_for(
            consumer(StubInvestigationService(), run).process_record(event_bytes(), LOCATION),
            timeout=0.5,
        )

        assert outcome is ProcessingOutcome.PROCESSED
        assert run.in_flight == 1  # still going

    async def test_a_slow_investigation_does_not_delay_the_next_record(self) -> None:
        workflow = StubWorkflow(delay=5.0)
        run = runner(workflow)
        subject = consumer(StubInvestigationService(), run)

        await asyncio.wait_for(subject.process_record(event_bytes("EX-1"), LOCATION), 0.5)
        await asyncio.wait_for(subject.process_record(event_bytes("EX-2"), LOCATION), 0.5)

        assert run.in_flight == 2

    async def test_nothing_is_scheduled_when_auto_run_is_disabled(self) -> None:
        """runner=None is what the flag being off looks like downstream."""
        investigations = StubInvestigationService()

        outcome = await consumer(investigations, None).process_record(event_bytes(), LOCATION)

        assert outcome is ProcessingOutcome.PROCESSED
        assert investigations.calls == ["EX-2001"]  # still recorded, just not run

    async def test_an_unusable_message_is_neither_recorded_nor_scheduled(self) -> None:
        workflow = StubWorkflow()
        run = runner(workflow)

        outcome = await consumer(StubInvestigationService(), run).process_record(
            b"not json", LOCATION
        )

        assert outcome is ProcessingOutcome.INVALID
        assert workflow.runs == []

    async def test_a_persistence_failure_still_reports_retry_later(self) -> None:
        # Kafka acknowledgement semantics are unchanged by automation.
        workflow = StubWorkflow()
        investigations = StubInvestigationService(raises=RuntimeError("database down"))

        outcome = await consumer(investigations, runner(workflow)).process_record(
            event_bytes(), LOCATION
        )

        assert outcome is ProcessingOutcome.RETRY_LATER
        assert workflow.runs == []

    async def test_a_scheduling_failure_does_not_change_the_offset_decision(self) -> None:
        """A recorded investigation must not be redelivered because scheduling broke."""

        class BrokenRunner:
            def schedule(self, investigation_id: str) -> None:
                raise RuntimeError("cannot schedule")

        outcome = await consumer(StubInvestigationService(), BrokenRunner()).process_record(
            event_bytes(), LOCATION
        )

        assert outcome is ProcessingOutcome.PROCESSED


# ---------------------------------------------------------------------------
# Idempotency: one exception, at most one paid run
# ---------------------------------------------------------------------------


class TestIdempotency:
    async def test_a_redelivered_event_is_not_scheduled_again(self) -> None:
        """created=False is what create_or_get returns for a duplicate."""
        workflow = StubWorkflow()
        run = runner(workflow)

        outcome = await consumer(
            StubInvestigationService(created=False), run
        ).process_record(event_bytes(), LOCATION)

        assert outcome is ProcessingOutcome.PROCESSED
        await run.drain(timeout=1)
        assert workflow.runs == []

    @pytest.mark.parametrize(
        "status", ["RUNNING", "AWAITING_REVIEW", "ESCALATED", "COMPLETED", "FAILED"]
    )
    async def test_a_non_pending_investigation_is_never_run_twice(self, status: str) -> None:
        """The workflow's conditional claim is the second line of defence.

        Even if something did schedule a duplicate, `_claim` only accepts
        PENDING, so every other status refuses — and the runner treats that
        refusal as normal rather than as an error worth retrying.
        """
        workflow = StubWorkflow(raises=InvestigationNotRunnable("INV-2001", status))
        run = runner(workflow)

        run.schedule("INV-2001")
        await run.drain(timeout=1)

        # Attempted once, refused, and not retried.
        assert workflow.runs == ["INV-2001"]

    async def test_an_unknown_investigation_is_not_retried(self) -> None:
        workflow = StubWorkflow(raises=InvestigationNotFound("INV-9999"))
        run = runner(workflow)

        run.schedule("INV-9999")
        await run.drain(timeout=1)

        assert workflow.runs == ["INV-9999"]


# ---------------------------------------------------------------------------
# Failures never reach the consumer loop
# ---------------------------------------------------------------------------


class TestFailureContainment:
    async def test_a_failing_investigation_does_not_raise_out_of_the_task(self) -> None:
        workflow = StubWorkflow(raises=RuntimeError("malformed result"))
        run = runner(workflow)

        task = run.schedule("INV-2001")
        await run.drain(timeout=1)

        # Not "Task exception was never retrieved": the task completed cleanly.
        assert task is not None
        assert task.done()
        assert task.exception() is None

    async def test_a_failing_investigation_does_not_stop_later_ones(self) -> None:
        workflow = StubWorkflow(raises=RuntimeError("malformed result"))
        run = runner(workflow)

        run.schedule("INV-1")
        run.schedule("INV-2")
        await run.drain(timeout=1)

        assert workflow.runs == ["INV-1", "INV-2"]


# ---------------------------------------------------------------------------
# Bounded retry, provider errors only
# ---------------------------------------------------------------------------


class TestRetry:
    async def test_a_provider_error_is_retried_up_to_the_bound(self) -> None:
        workflow = StubWorkflow(raises=InvestigationModelError("provider unreachable"))
        run = runner(workflow, max_attempts=2)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        assert workflow.runs == ["INV-2001", "INV-2001"]

    async def test_retries_are_bounded_and_do_not_loop_forever(self) -> None:
        workflow = StubWorkflow(raises=InvestigationModelError("provider unreachable"))
        run = runner(workflow, max_attempts=3)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        assert len(workflow.runs) == 3

    async def test_each_attempt_is_told_whether_another_follows(self) -> None:
        """The workflow records a scheduled retry only when one truly is coming.

        So the first attempt must be told a delay follows and the last must be
        told none does, or the audit trail would promise a retry that never
        happens.
        """
        workflow = StubWorkflow(raises=InvestigationModelError("provider unreachable"))
        run = runner(workflow, max_attempts=2)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        first, last = workflow.retry_contexts
        assert (first.attempt, first.max_attempts) == (1, 2)
        assert first.another_attempt_follows is True
        assert (last.attempt, last.max_attempts) == (2, 2)
        assert last.next_delay_seconds is None
        assert last.another_attempt_follows is False

    async def test_a_deterministic_failure_is_not_retried(self) -> None:
        """A malformed result would be malformed again. Retrying only spends money."""
        workflow = StubWorkflow(raises=RuntimeError("returned a malformed result"))
        run = runner(workflow, max_attempts=3)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        assert workflow.runs == ["INV-2001"]

    async def test_a_recovering_provider_succeeds_on_the_second_attempt(self) -> None:
        class Flaky(StubWorkflow):
            async def run(self, investigation_id: str, *, retry=None):
                self.runs.append(investigation_id)
                self.retry_contexts.append(retry)
                if len(self.runs) == 1:
                    raise InvestigationModelError("provider unreachable")
                return _outcome("ESCALATED")

        workflow = Flaky()
        run = runner(workflow, max_attempts=2)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        assert len(workflow.runs) == 2


# ---------------------------------------------------------------------------
# The shared budget
# ---------------------------------------------------------------------------


class TestBudget:
    async def test_an_automatic_run_consumes_global_budget(self) -> None:
        budget = limiter(global_limit=5)
        run = runner(StubWorkflow(), budget)

        run.schedule("INV-2001")
        await run.drain(timeout=1)

        # One unit gone: a manual run now sees four left.
        assert [budget.check("1.2.3.4").name for _ in range(4)] == ["ALLOWED"] * 4
        assert budget.check("1.2.3.4").name == "GLOBAL_LIMIT_REACHED"

    async def test_an_automatic_run_does_not_consume_a_per_client_allowance(self) -> None:
        """An event is not a visitor.

        Charging automation to a synthetic client key would exhaust an allowance
        describing nobody, and would throttle automation at the per-client limit
        rather than the global one.
        """
        budget = limiter(global_limit=100, per_client=1)
        run = runner(StubWorkflow(), budget)

        for _ in range(5):
            run.schedule("INV-2001")
        await run.drain(timeout=2)

        # A real client still has its full allowance.
        assert budget.check("1.2.3.4").name == "ALLOWED"

    async def test_manual_and_automatic_runs_share_one_ceiling(self) -> None:
        budget = limiter(global_limit=2)
        run = runner(StubWorkflow(), budget)

        assert budget.check("1.2.3.4").name == "ALLOWED"  # a manual run
        run.schedule("INV-2001")  # an automatic one
        await run.drain(timeout=1)

        assert budget.check("5.6.7.8").name == "GLOBAL_LIMIT_REACHED"

    async def test_each_retry_consumes_another_unit(self) -> None:
        budget = limiter(global_limit=2)
        workflow = StubWorkflow(raises=InvestigationModelError("provider unreachable"))
        run = runner(workflow, budget, max_attempts=5)

        run.schedule("INV-2001")
        await run.drain(timeout=2)

        # Two attempts, two units, then the budget stops it — not max_attempts.
        assert len(workflow.runs) == 2

    async def test_an_exhausted_budget_makes_no_provider_call_at_all(self) -> None:
        budget = limiter(global_limit=1)
        budget.check(None)  # spend the only unit
        workflow = StubWorkflow()
        run = runner(workflow, budget)

        run.schedule("INV-2001")
        await run.drain(timeout=1)

        # The investigation is untouched, so it stays PENDING and can run later.
        assert workflow.runs == []


# ---------------------------------------------------------------------------
# Concurrency and shutdown
# ---------------------------------------------------------------------------


class TestConcurrencyAndShutdown:
    async def test_concurrency_is_bounded_by_the_semaphore(self) -> None:
        workflow = StubWorkflow(delay=0.05)
        run = runner(workflow, concurrency=2)

        for index in range(6):
            run.schedule(f"INV-{index}")
        await run.drain(timeout=5)

        assert len(workflow.runs) == 6
        assert workflow.peak_concurrent <= 2

    async def test_drain_waits_for_in_flight_investigations(self) -> None:
        workflow = StubWorkflow(delay=0.05)
        run = runner(workflow)

        run.schedule("INV-2001")
        await run.drain(timeout=5)

        assert run.in_flight == 0

    async def test_drain_gives_up_rather_than_hanging(self) -> None:
        workflow = StubWorkflow(delay=10.0)
        run = runner(workflow)
        run.schedule("INV-2001")

        await asyncio.wait_for(run.drain(timeout=0.1), timeout=2)

        # Abandoned, not cancelled mid-write: the investigation's own
        # transaction has either committed or rolled back.
        assert run.in_flight == 1

    async def test_drain_is_safe_with_nothing_in_flight(self) -> None:
        await runner(StubWorkflow()).drain(timeout=1)

    async def test_the_consumer_drains_the_runner_on_stop(self) -> None:
        workflow = StubWorkflow(delay=0.05)
        run = runner(workflow)
        subject = consumer(StubInvestigationService(), run)

        await subject.process_record(event_bytes(), LOCATION)
        await subject.stop()  # no broker was started; stop is safe regardless

        assert run.in_flight == 0
        assert workflow.runs == ["INV-2001"]
