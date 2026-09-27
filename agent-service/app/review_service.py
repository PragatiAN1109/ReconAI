"""Human decisions on AI recommendations.

This is where authority lives. The AI proposes; only the code in this module
records that a person decided, and only a person's request reaches it.

**No method here calls the financial core.** Not POST, not PUT, not PATCH, not
DELETE — and no client is even injected, so the boundary is structural rather
than a rule to remember. Approving a recommendation records a human judgement
about an explanation. It does not resolve the exception, settle anything, or
move money. Acting on an approved recommendation stays a separate, deliberate
step outside this service.

Reviewer identity is caller-supplied and unauthenticated. This service has no
authentication, so ``reviewed_by`` is demo attribution: a claim recorded
verbatim, never a verified identity. Anything reading it must treat it that way.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import Sequence, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_service
from app.database import Database
from app.models import (
    SCHEMA,
    ActorType,
    AuditEventType,
    Investigation,
    InvestigationStatus,
    Recommendation,
    Review,
    ReviewDecision,
)

logger = logging.getLogger(__name__)

REVIEW_ID_SEQUENCE = Sequence("review_business_id_seq", schema=SCHEMA)
REVIEW_ID_PREFIX = "REV-"

#: A decision can only be made on an investigation that is waiting for one.
#: An ESCALATED investigation is not reviewable here: the guardrail already
#: routed it away from recommendation review and to a human investigating it
#: directly, which is a different activity with a different outcome.
_REVIEWABLE = InvestigationStatus.AWAITING_REVIEW

#: What each decision does to the investigation.
#:
#: REJECTED becomes ESCALATED rather than anything terminal-looking, because a
#: rejected recommendation does not make the discrepancy go away. The financial
#: exception is still there and still needs a human; what has been rejected is
#: one proposed explanation of it.
_RESULTING_STATUS = {
    ReviewDecision.APPROVED: InvestigationStatus.COMPLETED,
    ReviewDecision.REJECTED: InvestigationStatus.ESCALATED,
    ReviewDecision.ESCALATED: InvestigationStatus.ESCALATED,
}

_AUDIT_EVENT = {
    ReviewDecision.APPROVED: AuditEventType.REVIEW_APPROVED,
    ReviewDecision.REJECTED: AuditEventType.REVIEW_REJECTED,
    ReviewDecision.ESCALATED: AuditEventType.INVESTIGATION_ESCALATED,
}


class ReviewError(RuntimeError):
    """Base class for refusals this module raises."""


class InvestigationNotFound(ReviewError):
    """No investigation with that business identifier."""


class NotAwaitingReview(ReviewError):
    """The investigation is not waiting for a decision."""

    def __init__(self, investigation_id: str, current_status: str) -> None:
        super().__init__(
            f"Investigation {investigation_id} is {current_status}, not "
            f"{_REVIEWABLE.value}. Only an investigation awaiting review can be decided."
        )
        self.investigation_id = investigation_id
        self.current_status = current_status


class AlreadyReviewed(ReviewError):
    """A decision has already been recorded for this investigation."""

    def __init__(self, investigation_id: str) -> None:
        super().__init__(
            f"Investigation {investigation_id} has already been reviewed. "
            "A recorded decision is not replaced."
        )
        self.investigation_id = investigation_id


@dataclass(frozen=True)
class ReviewOutcome:
    """The recorded decision and the investigation it changed."""

    review: Review
    investigation: Investigation


class ReviewService:
    """Records human decisions. Holds no client to any other service."""

    def __init__(self, database: Database) -> None:
        # Deliberately the only dependency. There is nothing here to call the
        # financial core with, so approval cannot mutate a financial record
        # even by mistake.
        self._database = database

    async def decide(
        self,
        investigation_id: str,
        *,
        decision: ReviewDecision,
        reviewed_by: str,
        comment: str | None = None,
    ) -> ReviewOutcome:
        """Record one human decision, exactly once.

        The whole decision — the review row, the new status and the audit
        event — is one transaction. A decision recorded without its status
        change, or a status change with no record of who made it, would each be
        worse than neither.

        :raises InvestigationNotFound: no such investigation
        :raises NotAwaitingReview: it is not awaiting a decision
        :raises AlreadyReviewed: a decision already exists
        """
        reviewer = reviewed_by.strip()
        if not reviewer:
            # Rejected here as well as by the database, so the message names
            # the problem instead of surfacing a constraint violation.
            raise ValueError("reviewed_by must name the reviewer.")

        async with self._database.session() as session:
            # Conditional update, same reasoning as claiming a run: two
            # reviewers submitting at once both pass a read, but only one can
            # win an UPDATE ... WHERE status = 'AWAITING_REVIEW'.
            updated = (
                await session.execute(
                    update(Investigation)
                    .where(
                        Investigation.investigation_id == investigation_id,
                        Investigation.status == _REVIEWABLE.value,
                    )
                    .values(status=_RESULTING_STATUS[decision].value, updated_at=func.now())
                    .returning(Investigation)
                )
            ).scalar_one_or_none()

            if updated is None:
                raise await self._explain_refusal(session, investigation_id)

            recommendation = await session.scalar(
                select(Recommendation).where(
                    Recommendation.investigation_id == investigation_id
                )
            )
            if recommendation is None:  # pragma: no cover - AWAITING_REVIEW implies one exists
                raise ReviewError(
                    f"Investigation {investigation_id} is awaiting review but has no "
                    "recommendation to review."
                )

            review_id = await self._next_review_id(session)
            review = Review(
                review_id=review_id,
                investigation_id=investigation_id,
                recommendation_id=recommendation.recommendation_id,
                decision=decision.value,
                reviewed_by=reviewer,
                comment=comment,
            )
            session.add(review)

            try:
                await session.flush()
            except IntegrityError as error:
                # The unique constraint on investigation_id fired. The status
                # guard above makes this nearly unreachable; the database is
                # what makes "nearly" into "never".
                raise AlreadyReviewed(investigation_id) from error

            await audit_service.record(
                session,
                investigation_id=investigation_id,
                event_type=_AUDIT_EVENT[decision],
                actor_type=ActorType.HUMAN,
                # Unauthenticated, and recorded as the claim it is.
                actor_id=reviewer,
                metadata={
                    "review_id": review_id,
                    "recommendation_id": recommendation.recommendation_id,
                    "decision": decision.value,
                    "resulting_status": _RESULTING_STATUS[decision].value,
                    "has_comment": comment is not None,
                },
            )

        logger.info(
            "Review recorded [review_id=%s investigation_id=%s decision=%s status=%s]",
            review_id,
            investigation_id,
            decision.value,
            _RESULTING_STATUS[decision].value,
        )
        return ReviewOutcome(review=review, investigation=updated)

    async def get_for_investigation(self, investigation_id: str) -> Review | None:
        async with self._database.session() as session:
            return await session.scalar(
                select(Review).where(Review.investigation_id == investigation_id)
            )

    @staticmethod
    async def _explain_refusal(session: AsyncSession, investigation_id: str) -> ReviewError:
        """Work out why nothing was updated, so the caller gets a real reason."""
        existing = await session.scalar(
            select(Investigation).where(Investigation.investigation_id == investigation_id)
        )
        if existing is None:
            return InvestigationNotFound(f"Investigation {investigation_id} was not found.")
        return NotAwaitingReview(investigation_id, existing.status)

    @staticmethod
    async def _next_review_id(session: AsyncSession) -> str:
        value = await session.scalar(REVIEW_ID_SEQUENCE.next_value().select())
        return f"{REVIEW_ID_PREFIX}{value}"
