"""Automatic, event-driven investigation.

WHAT THIS IS FOR
----------------
A deterministic reconciliation exception should not wait for someone to press a
button before it is investigated. The human decision worth having is whether to
*act on* a recommendation, not whether to begin producing one.

So this runs the controlled investigation workflow in response to a Kafka event,
and nothing here relaxes what that workflow does: the read-only tool allowlist,
the evidence ledger, citation grounding, the structured-result contract, the
deterministic guardrail and human review are all unchanged and all still apply.
The only thing that changes is who starts it.

WHY IT IS NOT RUN INLINE IN THE CONSUMER
----------------------------------------
The consume loop is sequential — it awaits one record, commits its offset, then
takes the next. A provider call inside that loop would block every other
exception for as long as the model takes, and a hung call would stall the topic
entirely. Worse, a long enough investigation would exceed the consumer's poll
interval and trigger a group rebalance.

Instead the consumer commits the offset as it always has, and hands the
investigation to this runner as a detached task. Kafka acknowledgement semantics
are untouched, and provider latency is invisible to them.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a job queue, not a scheduler, not a distributed worker. There is no durable
record of "work to do" beyond the investigation row itself, and a crash loses
in-flight tasks — leaving those investigations RUNNING, which this service
already treats as an honest, operator-recoverable state. A portfolio demo does
not need a retry fabric, and pretending otherwise would be the more dishonest
choice.
"""

import asyncio
import logging
from collections.abc import Sequence

from app.investigation_model import InvestigationModelError
from app.investigation_workflow import (
    InvestigationNotFound,
    InvestigationNotRunnable,
    RetryContext,
)
from app.models import AuditEventType
from app.rate_limit import Decision, FixedWindowRateLimiter

logger = logging.getLogger(__name__)


class AutoInvestigationRunner:
    """Runs investigations in the background, within a budget and a bound.

    One instance per application. It owns the set of in-flight tasks so the
    consumer can drain them on shutdown, and the semaphore that stops a burst of
    exceptions from opening a burst of provider connections.
    """

    def __init__(
        self,
        workflow: object,
        limiter: FixedWindowRateLimiter,
        investigations: object | None = None,
        *,
        concurrency: int = 2,
        max_attempts: int = 2,
        backoff_seconds: Sequence[float] = (2.0, 5.0),
    ) -> None:
        self._workflow = workflow
        self._limiter = limiter
        # Used only to append operational audit events. Optional so the runner
        # remains unit-testable without a database.
        self._investigations = investigations
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_attempts = max_attempts
        self._backoff = tuple(backoff_seconds) or (2.0,)
        #: Strong references to in-flight tasks. Without this the event loop is
        #: free to garbage-collect a running task, and its exception would
        #: surface as "Task exception was never retrieved" with no context.
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    def schedule(self, investigation_id: str) -> asyncio.Task[None] | None:
        """Start an investigation in the background. Returns without waiting.

        The caller — the Kafka consumer — must not be delayed by this, so the
        budget check and the provider call both happen inside the task rather
        than here.
        """
        task = asyncio.create_task(
            self._run(investigation_id), name=f"auto-investigation-{investigation_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float) -> None:
        """Wait for in-flight investigations, up to ``timeout`` seconds.

        Not cancelled on timeout. Each investigation's persistence is its own
        transaction that either committed or rolled back, so abandoning the
        wait risks nothing partial — it only means the status may stay RUNNING,
        which is the same state a crash would leave and is already understood.
        """
        if not self._tasks:
            return

        pending = tuple(self._tasks)
        logger.info("Waiting for %d in-flight investigation(s) to finish", len(pending))
        done, still_running = await asyncio.wait(pending, timeout=timeout)

        if still_running:
            logger.warning(
                "Abandoning %d in-flight investigation(s) after %.1fs; they will remain "
                "RUNNING until recovered",
                len(still_running),
                timeout,
            )
        else:
            logger.info("All %d in-flight investigation(s) finished", len(done))

    async def _record_paused(
        self, investigation_id: str, *, reason: str, extra: dict
    ) -> None:
        """Record that automatic execution stopped without a conclusion.

        Not a failure: the investigation stays PENDING and is recoverable
        through the operator run endpoint. Calling this ``FAILED`` would be
        untrue and would make it unrecoverable, since FAILED is terminal and
        only PENDING may be run.

        Metadata is operational only — a reason code, counters, a retry-after —
        never a provider error string, which can embed request detail.
        """
        if self._investigations is None:
            return
        await self._investigations.record_operational_event(
            investigation_id,
            AuditEventType.INVESTIGATION_AUTO_RUN_PAUSED,
            {"reason": reason, **extra},
        )

    async def _run(self, investigation_id: str) -> None:
        """One investigation, with budget, bounded retry and no escaping errors.

        Every exception is handled here. This coroutine runs detached, so an
        exception escaping it would be reported by the event loop with no
        context and — more importantly — would tell nobody anything useful.
        """
        async with self._semaphore:
            for attempt in range(1, self._max_attempts + 1):
                # Checked per attempt, not per investigation: a retry is another
                # provider call and must cost another unit of the shared budget.
                # None means "global only" — an event has no client.
                decision = self._limiter.check(None)
                if decision is not Decision.ALLOWED:
                    retry_after = self._limiter.seconds_until_reset()
                    logger.warning(
                        "Automatic investigation paused: the AI budget for this window is "
                        "exhausted. The investigation stays PENDING and was not run "
                        "[investigation_id=%s attempt=%d decision=%s retry_after_seconds=%d]",
                        investigation_id,
                        attempt,
                        decision.name,
                        retry_after,
                    )
                    # Recorded, not merely logged: the detail page reads this to
                    # explain a PENDING investigation that nothing is going to
                    # pick up on its own.
                    await self._record_paused(
                        investigation_id,
                        reason="AI_BUDGET_EXHAUSTED",
                        extra={"retry_after_seconds": retry_after, "attempt": attempt},
                    )
                    return

                # The workflow needs to know whether another attempt follows,
                # so it can record a scheduled retry only when one truly is.
                delay = (
                    self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                    if attempt < self._max_attempts
                    else None
                )
                try:
                    outcome = await self._workflow.run(
                        investigation_id,
                        retry=RetryContext(
                            attempt=attempt,
                            max_attempts=self._max_attempts,
                            next_delay_seconds=delay,
                        ),
                    )
                except InvestigationNotRunnable:
                    # Already claimed, already concluded, or already failed.
                    # The commonest cause is a duplicate Kafka delivery, which
                    # is exactly what this refusal is for. Not an error.
                    logger.info(
                        "Automatic investigation skipped: not runnable "
                        "[investigation_id=%s]",
                        investigation_id,
                    )
                    return
                except InvestigationNotFound:
                    logger.error(
                        "Automatic investigation skipped: no such investigation "
                        "[investigation_id=%s]",
                        investigation_id,
                    )
                    return
                except InvestigationModelError as error:
                    # Retryable. The workflow has already released the
                    # investigation back to PENDING, so another attempt may
                    # claim it.
                    if attempt >= self._max_attempts or delay is None:
                        logger.warning(
                            "Automatic investigation gave up after %d provider failure(s); "
                            "the investigation stays PENDING "
                            "[investigation_id=%s failure_type=%s]",
                            attempt,
                            investigation_id,
                            type(error).__name__,
                        )
                        # Attempts exhausted, so no retry is coming. The
                        # workflow records a scheduled retry only when one is;
                        # this says automatic execution stopped.
                        await self._record_paused(
                            investigation_id,
                            reason="PROVIDER_UNAVAILABLE",
                            extra={
                                "attempts": attempt,
                                "failure_category": type(error).__name__,
                            },
                        )
                        return
                    logger.warning(
                        "Automatic investigation hit a provider failure; retrying in %.1fs "
                        "[investigation_id=%s attempt=%d of %d failure_type=%s]",
                        delay,
                        investigation_id,
                        attempt,
                        self._max_attempts,
                        type(error).__name__,
                    )
                    await asyncio.sleep(delay)
                    continue
                except Exception as error:
                    # Non-retryable: a malformed result, an ungrounded citation,
                    # an exhausted tool budget or a persistence failure. The
                    # workflow has already recorded FAILED with a reason. Retrying
                    # would spend money to reach the same conclusion.
                    logger.warning(
                        "Automatic investigation ended without a usable result "
                        "[investigation_id=%s failure_type=%s]",
                        investigation_id,
                        type(error).__name__,
                    )
                    return

                logger.info(
                    "Automatic investigation finished [investigation_id=%s status=%s attempt=%d]",
                    investigation_id,
                    outcome.decision.status.value,
                    attempt,
                )
                return
