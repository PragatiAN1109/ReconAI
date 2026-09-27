"""Read-only investigation endpoints.

There is no create endpoint. Investigations exist because the financial core
detected a discrepancy and said so on Kafka; letting a caller assert one into
existence would make this service a second, unverified source of truth.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

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
