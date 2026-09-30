"""Investigation endpoints: read, run, review, audit.

There is no create endpoint. Investigations exist because the financial core
detected a discrepancy and said so on Kafka; letting a caller assert one into
existence would make this service a second, unverified source of truth.

The review endpoints record a human decision and nothing else. None of them
calls the financial core, and approving a recommendation does not resolve an
exception or move money — see :mod:`app.review_service`.

**No endpoint here is authenticated.** ``reviewed_by`` is whatever the caller
sends. Every response that carries it says so, so a demo cannot be mistaken for
an access-controlled system.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.investigation_agent import InvestigationFailed
from app.investigation_model import InvestigationModelError
from app.investigation_workflow import (
    InvestigationNotFound,
    InvestigationNotRunnable,
    WorkflowOutcome,
)
from app.models import (
    AuditEvent,
    Investigation,
    Recommendation,
    RecommendationEvidence,
    Review,
    ReviewDecision,
)
from app.rate_limit import Decision
from app.review_service import AlreadyReviewed, NotAwaitingReview
from app.review_service import InvestigationNotFound as ReviewInvestigationNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"])

#: Stated on every response carrying a reviewer name, so no client can read one
#: as an authenticated identity.
REVIEWER_IS_UNVERIFIED = (
    "Reviewer identity is caller-supplied and unverified: this service has no "
    "authentication."
)


class InvestigationResponse(BaseModel):
    """One investigation.

    The internal UUID is not exposed; callers address investigations by
    business identifier, as they do everywhere else in ReconAI.
    """

    investigation_id: str
    exception_id: str
    transaction_id: str
    exception_type: str
    status: str
    detected_at: datetime
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def of(investigation: Investigation) -> "InvestigationResponse":
        return InvestigationResponse(
            investigation_id=investigation.investigation_id,
            exception_id=investigation.exception_id,
            transaction_id=investigation.transaction_id,
            exception_type=investigation.exception_type,
            status=investigation.status,
            detected_at=investigation.detected_at,
            created_at=investigation.created_at,
            updated_at=investigation.updated_at,
        )


class InvestigationListResponse(BaseModel):
    items: list[InvestigationResponse]
    total: int


class EvidenceResponse(BaseModel):
    """One verified reference behind a recommendation."""

    source_type: str
    reference: str
    section: str | None = None
    excerpt: str | None = None

    @staticmethod
    def of(evidence: RecommendationEvidence) -> "EvidenceResponse":
        return EvidenceResponse(
            source_type=evidence.source_type,
            reference=evidence.reference,
            section=evidence.section,
            excerpt=evidence.excerpt,
        )


class RecommendationResponse(BaseModel):
    """A stored AI conclusion, with what it rests on.

    ``confidence`` is the model's own self-reported number, serialised as a
    string so it survives JSON unrounded. It is **not a calibrated
    probability**: 0.9 does not mean nine such conclusions in ten are correct.
    It is an ordering signal, compared against an operator-chosen threshold, and
    a client must not present it as a statistical claim.
    """

    recommendation_id: str
    investigation_id: str
    classification: str
    root_cause: str
    confidence: Decimal
    confidence_note: str = (
        "Model self-reported, not a calibrated probability. Used only as an "
        "ordering signal against a configured review threshold."
    )
    recommended_action: str
    requires_human_approval: bool
    model_provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    created_at: datetime
    evidence: list[EvidenceResponse] = Field(default_factory=list)

    @staticmethod
    def of(
        recommendation: Recommendation, evidence: list[RecommendationEvidence]
    ) -> "RecommendationResponse":
        return RecommendationResponse(
            recommendation_id=recommendation.recommendation_id,
            investigation_id=recommendation.investigation_id,
            classification=recommendation.classification,
            root_cause=recommendation.root_cause,
            confidence=recommendation.confidence,
            recommended_action=recommendation.recommended_action,
            requires_human_approval=recommendation.requires_human_approval,
            model_provider=recommendation.model_provider,
            model_name=recommendation.model_name,
            prompt_version=recommendation.prompt_version,
            created_at=recommendation.created_at,
            evidence=[EvidenceResponse.of(item) for item in evidence],
        )


class ReviewResponse(BaseModel):
    """A recorded human decision."""

    review_id: str
    investigation_id: str
    recommendation_id: str
    decision: str
    reviewed_by: str
    reviewer_note: str = REVIEWER_IS_UNVERIFIED
    comment: str | None = None
    decided_at: datetime

    @staticmethod
    def of(review: Review) -> "ReviewResponse":
        return ReviewResponse(
            review_id=review.review_id,
            investigation_id=review.investigation_id,
            recommendation_id=review.recommendation_id,
            decision=review.decision,
            reviewed_by=review.reviewed_by,
            comment=review.comment,
            decided_at=review.decided_at,
        )


class RunResponse(BaseModel):
    """The outcome of a run, and why it was routed where it was.

    ``guardrail_reason`` is the deterministic policy's own explanation. It is
    generated by application code, not by the model, so it can be trusted as a
    description of why this investigation is awaiting review or escalated.

    ``evidence_retrieved`` is the application's record of what the tools
    actually returned, reported next to the model's citations so a reviewer can
    see the difference between what was available and what was used.
    """

    investigation_id: str
    status: str
    guardrail_reason: str
    recommendation: RecommendationResponse
    evidence_retrieved: dict[str, list[str]]

    @staticmethod
    def of(outcome: WorkflowOutcome) -> "RunResponse":
        ledger = outcome.ledger
        return RunResponse(
            investigation_id=outcome.investigation.investigation_id,
            status=outcome.decision.status.value,
            guardrail_reason=outcome.decision.reason,
            recommendation=RecommendationResponse(
                recommendation_id=outcome.recommendation.recommendation_id,
                investigation_id=outcome.recommendation.investigation_id,
                classification=outcome.result.classification.value,
                root_cause=outcome.result.root_cause,
                confidence=outcome.result.confidence_value,
                recommended_action=outcome.result.recommended_action,
                requires_human_approval=True,
                model_provider=outcome.recommendation.model_provider,
                model_name=outcome.recommendation.model_name,
                prompt_version=outcome.recommendation.prompt_version,
                created_at=outcome.recommendation.created_at,
                evidence=[
                    EvidenceResponse(
                        source_type=reference.source_type.value,
                        reference=reference.reference,
                        section=reference.section,
                        excerpt=ledger.excerpt_for(reference),
                    )
                    for reference in outcome.result.evidence
                ],
            ),
            evidence_retrieved={
                "transactions": sorted(ledger.transactions),
                "settlements": sorted(ledger.settlements),
                "feeRules": sorted(ledger.fee_rules),
                "policyDocuments": sorted(ledger.policy_documents),
            },
        )


class ReviewRequest(BaseModel):
    """A human decision submitted for an investigation.

    ``reviewed_by`` is required and recorded verbatim. It is not verified and
    not an identity: see the module docstring.
    """

    reviewed_by: str = Field(min_length=1, max_length=255)
    comment: str | None = Field(default=None, max_length=2000)


class AuditEventResponse(BaseModel):
    """One entry in the append-only trail."""

    event_id: str
    investigation_id: str
    event_type: str
    actor_type: str
    actor_id: str | None = None
    metadata: dict | None = None
    occurred_at: datetime

    @staticmethod
    def of(event: AuditEvent) -> "AuditEventResponse":
        return AuditEventResponse(
            event_id=event.event_id,
            investigation_id=event.investigation_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            metadata=event.metadata_json,
            occurred_at=event.occurred_at,
        )


class AuditListResponse(BaseModel):
    """An investigation's timeline, oldest first."""

    investigation_id: str
    items: list[AuditEventResponse]
    total: int


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("", response_model=InvestigationListResponse, summary="List investigations")
async def list_investigations(
    request: Request,
    exception_id: Annotated[
        str | None,
        Query(
            max_length=50,
            description=(
                "Return only the investigation recorded for this exception. An empty "
                "list means none exists yet, which is the expected answer while the "
                "event is still in flight."
            ),
        ),
    ] = None,
) -> InvestigationListResponse:
    """Every investigation, newest first, or just the one for an exception.

    The filter exists for a client that has caused an exception and is waiting
    for its investigation to appear. Polling the whole collection to find one
    row would get slower with every row ever created; this stays one lookup on a
    unique column.

    An unknown ``exception_id`` is an empty list, not a 404: "no investigation
    yet" is a normal stage of the lifecycle, not a missing resource.
    """
    if exception_id is not None:
        investigation = await request.app.state.investigations.find_by_exception_id(
            exception_id
        )
        items = [] if investigation is None else [InvestigationResponse.of(investigation)]
        return InvestigationListResponse(items=items, total=len(items))

    investigations = await request.app.state.investigations.list_all()
    items = [InvestigationResponse.of(investigation) for investigation in investigations]
    return InvestigationListResponse(items=items, total=len(items))


@router.get(
    "/{investigation_id}",
    response_model=InvestigationResponse,
    summary="Get one investigation",
)
async def get_investigation(request: Request, investigation_id: str) -> InvestigationResponse:
    investigation = await _require_investigation(request, investigation_id)
    return InvestigationResponse.of(investigation)


@router.get(
    "/{investigation_id}/recommendation",
    response_model=RecommendationResponse,
    summary="Get the stored recommendation for an investigation",
)
async def get_recommendation(request: Request, investigation_id: str) -> RecommendationResponse:
    """The AI's durable conclusion and the evidence behind it.

    404 while an investigation is still PENDING or RUNNING: there is genuinely
    no conclusion yet, and returning an empty one would invite a client to
    render a recommendation that does not exist.
    """
    await _require_investigation(request, investigation_id)

    recommendation = await request.app.state.investigations.get_recommendation(investigation_id)
    if recommendation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation {investigation_id} has no recommendation yet.",
        )
    evidence = await request.app.state.investigations.get_evidence(
        recommendation.recommendation_id
    )
    return RecommendationResponse.of(recommendation, evidence)


@router.get(
    "/{investigation_id}/audit",
    response_model=AuditListResponse,
    summary="Get an investigation's audit trail",
)
async def get_audit_trail(request: Request, investigation_id: str) -> AuditListResponse:
    """Read-only, and the only audit endpoint there is.

    The trail is append-only: there is no endpoint to amend or delete an entry,
    by design.
    """
    await _require_investigation(request, investigation_id)

    events = await request.app.state.audit.list_for_investigation(investigation_id)
    return AuditListResponse(
        investigation_id=investigation_id,
        items=[AuditEventResponse.of(event) for event in events],
        total=len(events),
    )


# ---------------------------------------------------------------------------
# Running an investigation
# ---------------------------------------------------------------------------


def _client_key(request: Request) -> str:
    """Best-effort caller identity for throttling.

    In the deployed stack every request arrives through CloudFront and an ALB,
    so ``request.client`` is the load balancer and useless as a key. The
    leftmost ``X-Forwarded-For`` entry is the viewer address.

    A caller can set that header, so this identifies a caller only as well as a
    throttle for a public demo needs to. The global limit is what actually caps
    spend and cannot be sidestepped this way.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _throttle_detail(decision: Decision, retry_after: int) -> str:
    shared = f" This is a shared public demo; please retry in {retry_after}s."
    if decision is Decision.GLOBAL_LIMIT_REACHED:
        return (
            "The demo has reached its overall limit for AI investigation runs in this "
            "window." + shared
        )
    return "You have reached the limit for AI investigation runs in this window." + shared


@router.post(
    "/{investigation_id}/run",
    response_model=RunResponse,
    summary="Run an investigation and record its outcome",
)
async def run_investigation(
    request: Request, investigation_id: str, response: Response
) -> RunResponse:
    """Investigate one exception, store the result, and route it for review.

    Investigation only. It does not approve anything, does not write to the
    financial core, and does not complete itself: the guardrail can send a
    result to a human or escalate it, and nothing else.

    Runnable once. A second call while the first is running, or after a result
    exists, is refused with 409 rather than producing a second conclusion.

    This is the only operation in ReconAI that spends money, and this service is
    public and unauthenticated, so it is throttled before any provider call is
    made. A refused run does not touch the investigation: nothing is recorded,
    no status changes, and the caller can try again in the next window.
    """
    workflow = request.app.state.investigation_workflow
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No investigation model is configured. Set RECONAI_AGENT_LLM_PROVIDER "
                "and RECONAI_AGENT_LLM_API_KEY."
            ),
        )

    # Checked before the workflow is entered, so a throttled request costs
    # nothing — no database transaction, no status transition, no provider call.
    limiter = request.app.state.run_limiter
    decision = limiter.check(_client_key(request))
    if decision is not Decision.ALLOWED:
        retry_after = limiter.seconds_until_reset()
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_throttle_detail(decision, retry_after),
            headers={"Retry-After": str(retry_after)},
        )

    try:
        outcome = await workflow.run(investigation_id)
    except InvestigationNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except InvestigationNotRunnable as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except InvestigationModelError as error:
        # The provider could not be reached. Distinct from a failed
        # investigation: nothing is wrong with the exception under examination,
        # so this is a dependency outage rather than an unprocessable request.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except InvestigationFailed as error:
        # Includes ungrounded citations. A failed investigation has no
        # conclusion, and none is invented to fill the gap.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error

    return RunResponse.of(outcome)


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------


@router.post(
    "/{investigation_id}/approve",
    response_model=ReviewResponse,
    summary="Approve a recommendation (human decision)",
)
async def approve(
    request: Request, investigation_id: str, body: ReviewRequest
) -> ReviewResponse:
    """Record that a human accepted this explanation.

    Approval is a judgement about an explanation, not an instruction to act on
    it. This endpoint writes to this service's own tables and makes no call to
    the financial core: no exception is resolved, no settlement is altered, and
    no money moves. Acting on an approved recommendation is a separate,
    deliberate step outside this service.
    """
    return await _decide(request, investigation_id, ReviewDecision.APPROVED, body)


@router.post(
    "/{investigation_id}/reject",
    response_model=ReviewResponse,
    summary="Reject a recommendation (human decision)",
)
async def reject(request: Request, investigation_id: str, body: ReviewRequest) -> ReviewResponse:
    """Record that a human did not accept this explanation.

    The investigation becomes ESCALATED, not resolved. Rejecting an explanation
    does not make the underlying discrepancy disappear — it still exists and
    still needs a human.
    """
    return await _decide(request, investigation_id, ReviewDecision.REJECTED, body)


@router.post(
    "/{investigation_id}/escalate",
    response_model=ReviewResponse,
    summary="Escalate an investigation (human decision)",
)
async def escalate(
    request: Request, investigation_id: str, body: ReviewRequest
) -> ReviewResponse:
    """Record that a human declined to decide and passed it on.

    Distinct from rejection: the explanation is not being judged wrong, it is
    being judged above this reviewer's authority. The distinction is worth
    keeping because the two say different things to whoever picks it up next.
    """
    return await _decide(request, investigation_id, ReviewDecision.ESCALATED, body)


async def _decide(
    request: Request,
    investigation_id: str,
    decision: ReviewDecision,
    body: ReviewRequest,
) -> ReviewResponse:
    try:
        outcome = await request.app.state.reviews.decide(
            investigation_id,
            decision=decision,
            reviewed_by=body.reviewed_by,
            comment=body.comment,
        )
    except ReviewInvestigationNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
    except (NotAwaitingReview, AlreadyReviewed) as error:
        # 409, not 400: the request is well-formed, and it is the state of the
        # investigation that makes it impossible.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return ReviewResponse.of(outcome.review)


async def _require_investigation(request: Request, investigation_id: str) -> Investigation:
    investigation = await request.app.state.investigations.get_by_investigation_id(
        investigation_id
    )
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation {investigation_id} was not found.",
        )
    return investigation
