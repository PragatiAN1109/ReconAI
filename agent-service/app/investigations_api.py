"""Read-only investigation endpoints.

There is no create endpoint. Investigations exist because the financial core
detected a discrepancy and said so on Kafka; letting a caller assert one into
existence would make this service a second, unverified source of truth.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.events import ExceptionType
from app.investigation_agent import InvestigationFailed
from app.investigation_model import InvestigationModelError
from app.investigation_models import InvestigationContext, InvestigationResult
from app.models import Investigation

router = APIRouter(prefix="/api/v1/investigations", tags=["investigations"])


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


@router.get("", response_model=InvestigationListResponse, summary="List investigations")
async def list_investigations(request: Request) -> InvestigationListResponse:
    investigations = await request.app.state.investigations.list_all()
    items = [InvestigationResponse.of(investigation) for investigation in investigations]
    return InvestigationListResponse(items=items, total=len(items))


@router.get(
    "/{investigation_id}",
    response_model=InvestigationResponse,
    summary="Get one investigation",
)
async def get_investigation(request: Request, investigation_id: str) -> InvestigationResponse:
    investigation = await request.app.state.investigations.get_by_investigation_id(investigation_id)
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation {investigation_id} was not found.",
        )
    return InvestigationResponse.of(investigation)


class InvestigationResultResponse(BaseModel):
    """The outcome of running an investigation, with what it rests on.

    ``evidence_retrieved`` is the application's own record of what tools
    returned, reported alongside the model's citations so a reviewer can see
    the difference between what was available and what was cited.
    """

    investigation_id: str
    result: InvestigationResult
    evidence_retrieved: dict[str, list[str]]


@router.post(
    "/{investigation_id}/run",
    response_model=InvestigationResultResponse,
    summary="Run an investigation (development entry point)",
)
async def run_investigation(
    request: Request, investigation_id: str
) -> InvestigationResultResponse:
    """Investigate one exception and return the validated result.

    Reasoning only. It does not approve anything, does not modify a financial
    record, does not persist the result, and does not move the investigation out
    of PENDING — persistence and lifecycle are a later phase. Calling this twice
    runs the investigation twice and changes nothing either time.
    """
    investigation = await request.app.state.investigations.get_by_investigation_id(
        investigation_id
    )
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation {investigation_id} was not found.",
        )

    agent = request.app.state.investigation_agent
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No investigation model is configured. Set RECONAI_AGENT_LLM_PROVIDER "
                "and RECONAI_AGENT_LLM_API_KEY."
            ),
        )

    context = InvestigationContext(
        investigationId=investigation.investigation_id,
        exceptionId=investigation.exception_id,
        transactionId=investigation.transaction_id,
        exceptionType=ExceptionType(investigation.exception_type),
    )

    try:
        result, ledger = await agent.investigate(context)
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

    return InvestigationResultResponse(
        investigation_id=investigation.investigation_id,
        result=result,
        evidence_retrieved={
            "transactions": sorted(ledger.transactions),
            "settlements": sorted(ledger.settlements),
            "feeRules": sorted(ledger.fee_rules),
            "policyDocuments": sorted(ledger.policy_documents),
        },
    )
