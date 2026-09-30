"""Running an investigation and durably recording what it concluded.

The shape of this module is dictated by one fact: a language model call is slow,
external, and cannot participate in a database transaction. Pretending otherwise
would mean holding a row lock open across a network call to a third party.

So the work is split into three short transactions with the model call between
them, and nothing is held open while it runs:

1. **Claim.** Move PENDING to RUNNING with a conditional update. Commit.
2. **Investigate.** No transaction is open. This is where the model and the
   controlled tools do their work.
3. **Record.** Persist the recommendation, its verified evidence, the guardrail
   outcome and the audit events. Commit.

Step 3 is one transaction. A failure anywhere inside it — the recommendation,
any evidence row, the status transition, either audit event — rolls the whole
thing back, so a partial result cannot exist. A fourth, separate transaction
then records RUNNING -> FAILED, because the rolled-back one cannot.

A *crash* between 1 and 3 leaves the investigation in RUNNING, since no failure
handler runs. That is a visible, honest state — the work was started and its
outcome is unknown — and recovering from it is a deliberate operational
decision, not something a background process should quietly undo. There is no
scheduler and no distributed lock here.

Nothing in this module approves anything, and nothing in it writes to the
financial core.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Sequence, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_service, guardrails
from app.database import Database
from app.evidence_ledger import EvidenceLedger
from app.events import ExceptionType
from app.guardrails import GuardrailDecision
from app.investigation_agent import InvestigationFailed
from app.investigation_model import InvestigationModelError
from app.investigation_models import InvestigationContext, InvestigationResult
from app.models import (
    SCHEMA,
    ActorType,
    AuditEventType,
    Investigation,
    InvestigationStatus,
    Recommendation,
    RecommendationEvidence,
)

logger = logging.getLogger(__name__)

RECOMMENDATION_ID_SEQUENCE = Sequence("recommendation_business_id_seq", schema=SCHEMA)
RECOMMENDATION_ID_PREFIX = "REC-"

#: The statuses a run may be started from. RUNNING is excluded deliberately:
#: another run is already in progress, or one died partway. Either way, starting
#: a second is not the answer.
_RUNNABLE = frozenset({InvestigationStatus.PENDING})


class WorkflowError(RuntimeError):
    """Base class for refusals this module raises."""


class InvestigationNotFound(WorkflowError):
    """No investigation with that business identifier."""


class InvestigationNotRunnable(WorkflowError):
    """The investigation is not in a state where a run may begin.

    Carries the status found, because "already running" and "already reviewed"
    are very different things to the person who made the request.
    """

    def __init__(self, investigation_id: str, current_status: str) -> None:
        super().__init__(
            f"Investigation {investigation_id} cannot be run from status {current_status}. "
            f"Only {', '.join(sorted(status.value for status in _RUNNABLE))} may be run."
        )
        self.investigation_id = investigation_id
        self.current_status = current_status


def _failure_detail(error: Exception) -> str:
    """A description of a failure that is safe to store in the audit trail.

    Only messages this service wrote are copied verbatim. A database error's
    string is not: SQLAlchemy embeds the failing statement *and its bound
    parameters*, so copying it would write row contents — a root cause, an
    amount, an identifier — into a table whose stated guarantee is that it holds
    no sensitive detail.

    The exception type is recorded separately and is enough to tell an operator
    what kind of thing went wrong; the specifics belong in the logs.
    """
    if isinstance(error, InvestigationFailed | InvestigationModelError | WorkflowError):
        return str(error)[:500]
    return (
        "A persistence error occurred while recording the result. The transaction "
        "was rolled back and nothing was stored. See the service logs for detail."
    )


@dataclass(frozen=True)
class RetryContext:
    """Where this attempt sits in a bounded sequence of them.

    Supplied by whoever is doing the retrying — the automatic runner. The
    workflow uses it for one thing: deciding whether "a retry is scheduled" is a
    true statement to put in the audit trail. A manual run supplies nothing and
    is treated as a single attempt with no successor.
    """

    attempt: int
    max_attempts: int
    #: Seconds until the next attempt, or ``None`` when this was the last one.
    next_delay_seconds: float | None

    @property
    def another_attempt_follows(self) -> bool:
        return self.next_delay_seconds is not None and self.attempt < self.max_attempts


@dataclass(frozen=True)
class WorkflowOutcome:
    """What a completed run produced."""

    investigation: Investigation
    recommendation: Recommendation
    result: InvestigationResult
    ledger: EvidenceLedger
    decision: GuardrailDecision


class InvestigationWorkflow:
    """Runs one investigation and records its outcome."""

    def __init__(
        self,
        database: Database,
        agent: object,
        *,
        confidence_threshold: Decimal,
        minimum_evidence: int,
        model_provider: str | None = None,
        model_name: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._database = database
        self._agent = agent
        self._confidence_threshold = confidence_threshold
        self._minimum_evidence = minimum_evidence
        self._model_provider = model_provider
        self._model_name = model_name
        self._prompt_version = prompt_version

    async def run(
        self, investigation_id: str, *, retry: RetryContext | None = None
    ) -> WorkflowOutcome:
        """Investigate one exception, persist the outcome, and route it.

        :raises InvestigationNotFound: no such investigation
        :raises InvestigationNotRunnable: it is not PENDING
        :raises InvestigationModelError: the provider could not be reached
        :raises InvestigationFailed: no grounded conclusion was reached
        """
        investigation = await self._claim(investigation_id)
        context = InvestigationContext(
            investigationId=investigation.investigation_id,
            exceptionId=investigation.exception_id,
            transactionId=investigation.transaction_id,
            exceptionType=ExceptionType(investigation.exception_type),
        )

        # No transaction is open across this call. It is slow, external, and
        # failure-prone, and a database connection held through it would be a
        # connection held hostage by a third party's latency.
        try:
            result, ledger = await self._agent.investigate(context)
        except InvestigationModelError as error:
            # RETRYABLE. The provider could not be reached, timed out, or
            # answered with something the adapter could not use. Nothing is
            # wrong with the exception under examination, so FAILED would be
            # both untrue and unrecoverable: FAILED is terminal and _RUNNABLE
            # admits only PENDING, so a two-second outage would strand the
            # investigation forever. Released back to PENDING instead, which
            # truthfully means "not yet run" and can be run again.
            await self._release_for_retry(investigation_id, error, retry)
            raise
        except InvestigationFailed as error:
            # NON-RETRYABLE. A malformed result, an ungrounded citation, a
            # text-only turn or an exhausted tool budget are all deterministic
            # outcomes of this exception and this prompt. Running it again would
            # spend money to reach the same place, so it ends here.
            await self._record_failure(investigation_id, error)
            raise

        try:
            return await self._record_result(investigation_id, result, ledger)
        except InvestigationNotRunnable:
            # A concurrent run already recorded a recommendation. That run's
            # outcome is valid and the investigation is whatever it set; marking
            # it FAILED here would destroy someone else's correct result to
            # report our own redundant one.
            raise
        except Exception as error:
            # The result transaction rolled back, so nothing partial survives —
            # but the investigation is still RUNNING, which would be a lie: the
            # run is over and produced nothing durable. A separate transaction
            # records that.
            logger.exception(
                "Persisting the investigation result failed; the transaction was rolled "
                "back and nothing was stored [investigation_id=%s]",
                investigation_id,
            )
            await self._record_failure(investigation_id, error)
            raise

    async def _claim(self, investigation_id: str) -> Investigation:
        """Take PENDING to RUNNING, or refuse.

        A conditional UPDATE, not a read followed by a write. Two concurrent
        requests both pass a read; only one can win an
        ``UPDATE ... WHERE status = 'PENDING'``, because the database serialises
        the row. The loser gets zero rows back and is told no.

        This is the only concurrency control the run path needs, and it is the
        database's, not the application's.
        """
        async with self._database.session() as session:
            claimed = (
                await session.execute(
                    update(Investigation)
                    .where(
                        Investigation.investigation_id == investigation_id,
                        Investigation.status == InvestigationStatus.PENDING.value,
                    )
                    .values(status=InvestigationStatus.RUNNING.value, updated_at=func.now())
                    .returning(Investigation)
                )
            ).scalar_one_or_none()

            if claimed is None:
                # Nothing was claimed. Either it does not exist, or someone
                # else holds it — and the caller deserves to know which.
                existing = await session.scalar(
                    select(Investigation).where(
                        Investigation.investigation_id == investigation_id
                    )
                )
                if existing is None:
                    raise InvestigationNotFound(
                        f"Investigation {investigation_id} was not found."
                    )
                logger.info(
                    "Refused to run an investigation that is not PENDING "
                    "[investigation_id=%s status=%s]",
                    investigation_id,
                    existing.status,
                )
                raise InvestigationNotRunnable(investigation_id, existing.status)

            await audit_service.record(
                session,
                investigation_id=investigation_id,
                event_type=AuditEventType.INVESTIGATION_STARTED,
                actor_type=ActorType.SYSTEM,
                metadata={"model_provider": self._model_provider, "model_name": self._model_name},
            )
            logger.info("Investigation claimed [investigation_id=%s]", investigation_id)
            return claimed

    async def _record_result(
        self, investigation_id: str, result: InvestigationResult, ledger: EvidenceLedger
    ) -> WorkflowOutcome:
        """Persist the recommendation, apply the guardrail, and audit both.

        One transaction. The recommendation, its evidence, the resulting status
        and the audit events are a single fact about what happened, and a
        partial version of it would be worse than none: a recommendation with no
        status change is invisible, and a status change with no recommendation
        is unexplainable.
        """
        # Deterministic, and computed before anything is written, so the stored
        # status and the stored confidence can never disagree.
        decision = guardrails.evaluate(
            result,
            confidence_threshold=self._confidence_threshold,
            minimum_evidence=self._minimum_evidence,
        )

        async with self._database.session() as session:
            recommendation_id = await self._next_recommendation_id(session)
            recommendation = Recommendation(
                recommendation_id=recommendation_id,
                investigation_id=investigation_id,
                classification=result.classification.value,
                root_cause=result.root_cause,
                confidence=result.confidence_value,
                recommended_action=result.recommended_action,
                # Pinned true by the result schema as well; stated here so the
                # stored row does not depend on a default holding.
                requires_human_approval=True,
                model_provider=self._model_provider,
                model_name=self._model_name,
                prompt_version=self._prompt_version,
            )
            session.add(recommendation)

            try:
                await session.flush()
            except IntegrityError as error:
                # The unique constraint on investigation_id fired: a concurrent
                # run got there first. Surfaced rather than swallowed — two runs
                # of one investigation is a real anomaly, even though the
                # database has already prevented the damage.
                logger.warning(
                    "A recommendation already exists for this investigation "
                    "[investigation_id=%s]",
                    investigation_id,
                )
                raise InvestigationNotRunnable(
                    investigation_id, InvestigationStatus.RUNNING.value
                ) from error

            # Only references the ledger vouched for reach storage. The agent
            # has already rejected results citing anything else, so this is the
            # second of two gates rather than the only one.
            for reference in result.evidence:
                session.add(
                    RecommendationEvidence(
                        recommendation_id=recommendation_id,
                        source_type=reference.source_type.value,
                        reference=reference.reference,
                        section=reference.section,
                        excerpt=ledger.excerpt_for(reference),
                    )
                )

            await session.execute(
                update(Investigation)
                .where(Investigation.investigation_id == investigation_id)
                .values(status=decision.status.value, updated_at=func.now())
            )

            await audit_service.record(
                session,
                investigation_id=investigation_id,
                event_type=AuditEventType.AI_RESULT_GENERATED,
                actor_type=ActorType.AI,
                metadata={
                    "recommendation_id": recommendation_id,
                    "classification": result.classification.value,
                    # A string, not a float: this is the exact value stored, and
                    # JSON would otherwise round it.
                    "confidence": str(result.confidence_value),
                    "evidence_count": len(result.evidence),
                    "model_name": self._model_name,
                    "prompt_version": self._prompt_version,
                },
            )
            await audit_service.record(
                session,
                investigation_id=investigation_id,
                event_type=(
                    AuditEventType.INVESTIGATION_ESCALATED
                    if decision.escalated
                    else AuditEventType.INVESTIGATION_AWAITING_REVIEW
                ),
                actor_type=ActorType.SYSTEM,
                metadata={
                    "recommendation_id": recommendation_id,
                    "reason": decision.reason,
                    "confidence_threshold": str(self._confidence_threshold),
                    "minimum_evidence": self._minimum_evidence,
                },
            )

            investigation = await session.scalar(
                select(Investigation).where(Investigation.investigation_id == investigation_id)
            )

        logger.info(
            "Investigation recorded [investigation_id=%s recommendation_id=%s "
            "classification=%s status=%s]",
            investigation_id,
            recommendation_id,
            result.classification.value,
            decision.status.value,
        )
        return WorkflowOutcome(
            investigation=investigation,
            recommendation=recommendation,
            result=result,
            ledger=ledger,
            decision=decision,
        )

    async def _release_for_retry(
        self,
        investigation_id: str,
        error: Exception,
        retry: RetryContext | None = None,
    ) -> None:
        """Return a claimed investigation to PENDING after a retryable failure.

        Its own transaction, and it never raises, for the same reasons as
        :meth:`_record_failure`.

        **Why PENDING and not FAILED.** ``_RUNNABLE`` admits only PENDING, and
        FAILED is terminal, so recording a provider outage as FAILED would make
        a transient network error permanently unrecoverable. PENDING means "not
        yet run", which is exactly what is true here.

        **The audit event.** ``INVESTIGATION_RETRY_SCHEDULED`` is written only
        when another attempt genuinely follows. Writing it when attempts are
        exhausted would promise a retry that is not coming; that case is
        recorded by the runner as ``INVESTIGATION_AUTO_RUN_PAUSED`` instead,
        because "automatic execution stopped" is a fact about the runner rather
        than about this transaction.

        Metadata carries the attempt counters, the backoff and the exception
        *class* — never the provider's error string, which can embed request
        detail the audit trail promises not to hold.
        """
        try:
            async with self._database.session() as session:
                await session.execute(
                    update(Investigation)
                    .where(
                        Investigation.investigation_id == investigation_id,
                        Investigation.status == InvestigationStatus.RUNNING.value,
                    )
                    .values(status=InvestigationStatus.PENDING.value, updated_at=func.now())
                )
                if retry is not None and retry.another_attempt_follows:
                    await audit_service.record(
                        session,
                        investigation_id=investigation_id,
                        event_type=AuditEventType.INVESTIGATION_RETRY_SCHEDULED,
                        actor_type=ActorType.SYSTEM,
                        metadata={
                            "attempt": retry.attempt,
                            "max_attempts": retry.max_attempts,
                            "backoff_seconds": retry.next_delay_seconds,
                            "failure_category": type(error).__name__,
                        },
                    )
        except Exception:
            logger.exception(
                "Could not release the investigation; it remains RUNNING "
                "[investigation_id=%s]",
                investigation_id,
            )
            return

        logger.warning(
            "Investigation released for retry after a provider failure; it is PENDING again "
            "[investigation_id=%s failure_type=%s detail=%s]",
            investigation_id,
            type(error).__name__,
            _failure_detail(error),
        )

    async def _record_failure(self, investigation_id: str, error: Exception) -> None:
        """Move a failed run to FAILED and say so in the trail.

        Its own transaction, because any transaction that was open has already
        been rolled back. This is the "new transaction" half of the failure
        path: the result transaction is atomic and leaves nothing behind, and
        this one records that the run ended.

        Never raises. It is always called while another exception is
        propagating, and masking the real cause with a secondary failure would
        turn a diagnosable problem into a mysterious one. If this write cannot
        happen the investigation stays RUNNING, which is accurate: we could not
        record that it stopped.
        """
        try:
            async with self._database.session() as session:
                await session.execute(
                    update(Investigation)
                    .where(Investigation.investigation_id == investigation_id)
                    .values(status=InvestigationStatus.FAILED.value, updated_at=func.now())
                )
                await audit_service.record(
                    session,
                    investigation_id=investigation_id,
                    event_type=AuditEventType.INVESTIGATION_FAILED,
                    actor_type=ActorType.SYSTEM,
                    metadata={
                        "failure_type": type(error).__name__,
                        "detail": _failure_detail(error),
                    },
                )
        except Exception:
            logger.exception(
                "Could not record the failure; the investigation remains RUNNING "
                "[investigation_id=%s]",
                investigation_id,
            )
            return

        logger.warning(
            "Investigation failed [investigation_id=%s failure_type=%s]",
            investigation_id,
            type(error).__name__,
        )

    @staticmethod
    async def _next_recommendation_id(session: AsyncSession) -> str:
        value = await session.scalar(RECOMMENDATION_ID_SEQUENCE.next_value().select())
        return f"{RECOMMENDATION_ID_PREFIX}{value}"
