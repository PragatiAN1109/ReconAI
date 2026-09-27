"""What an investigation is asked, and what it is allowed to answer.

The classification an investigation proposes is a different thing from the
discrepancy reconciliation detected, and the two enums are deliberately
separate. Reconciliation says ``AMOUNT_MISMATCH`` — two authoritative records
disagree. An investigation may later say ``PROCESSOR_FEE`` — here is why. The
first is a fact; the second is a conclusion requiring evidence and human review.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events import ExceptionType

# Same strictness as every other contract in this service: unexpected fields are
# rejected rather than ignored. A model inventing an extra field is exactly the
# drift worth hearing about.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class RootCauseClassification(StrEnum):
    """Why a discrepancy exists (docs/data-model.md section 9).

    Deliberately different from ``ExceptionType``. ``PROCESSOR_FEE`` appears
    here and must never appear there: the financial core cannot know why two
    records disagree, and an investigation's conclusion is not a detection.

    ``INSUFFICIENT_EVIDENCE`` is a first-class successful outcome, not a
    failure. An investigation that cannot support a conclusion should say so
    rather than pick the most plausible-sounding option.
    """

    PROCESSOR_FEE = "PROCESSOR_FEE"
    PROCESSOR_DELAY = "PROCESSOR_DELAY"
    DUPLICATE_PROCESSING = "DUPLICATE_PROCESSING"
    CURRENCY_CONVERSION = "CURRENCY_CONVERSION"
    PROCESSOR_ERROR = "PROCESSOR_ERROR"
    UNKNOWN = "UNKNOWN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceSource(StrEnum):
    """Kinds of evidence an investigation may cite (docs/data-model.md section 7).

    One per controlled tool. ``HISTORICAL_TRANSACTION`` from the documented set
    is absent because no tool retrieves it yet; a source with no tool behind it
    could only ever be cited from imagination.
    """

    TRANSACTION = "TRANSACTION"
    SETTLEMENT = "SETTLEMENT"
    FEE_RULE = "FEE_RULE"
    POLICY_DOCUMENT = "POLICY_DOCUMENT"


class InvestigationContext(BaseModel):
    """What the investigator is asked about.

    Identifiers and the detected discrepancy type, and nothing else. The
    persisted investigation row is not handed over wholesale: internal UUIDs and
    storage timestamps tell an investigator nothing and would only widen what a
    prompt can leak.

    Everything else must be retrieved through tools, which is what makes the
    evidence ledger meaningful.
    """

    model_config = _STRICT

    investigation_id: str = Field(alias="investigationId")
    exception_id: str = Field(alias="exceptionId")
    transaction_id: str = Field(alias="transactionId")
    exception_type: ExceptionType = Field(alias="exceptionType")


class EvidenceReference(BaseModel):
    """A pointer to something a tool actually returned.

    Structured rather than prose. "According to the fee schedule" cannot be
    checked; ``FEE_RULE / FR-14`` can, and is — every reference is verified
    against the evidence ledger before a result is accepted.

    ``section`` distinguishes two excerpts from the same policy document.
    """

    model_config = _STRICT

    source_type: EvidenceSource = Field(alias="sourceType")
    reference: str = Field(min_length=1)
    section: str | None = None

    def describe(self) -> str:
        return f"{self.source_type.value}/{self.reference}" + (
            f"§{self.section}" if self.section else ""
        )


class InvestigationResult(BaseModel):
    """A proposed explanation, and the evidence it rests on.

    Advisory. Nothing in this service acts on it, and ``requires_human_approval``
    is pinned true so a result cannot describe itself as needing no review.
    """

    model_config = _STRICT

    classification: RootCauseClassification
    root_cause: str = Field(alias="rootCause", min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommended_action: str = Field(alias="recommendedAction", min_length=1, max_length=2000)
    requires_human_approval: bool = Field(default=True, alias="requiresHumanApproval")

    @field_validator("requires_human_approval")
    @classmethod
    def _approval_is_always_required(cls, value: bool) -> bool:
        """V1 invariant: no investigation result may waive human review.

        Enforced in the schema rather than checked afterwards, so a model cannot
        produce a result that claims to be self-approving — not even a malformed
        one that slips past a later check.
        """
        if not value:
            raise ValueError(
                "requiresHumanApproval must be true: investigation results are advisory"
            )
        return value

    def cites(self) -> list[str]:
        return [reference.describe() for reference in self.evidence]


def difference(expected: Decimal, settled: Decimal) -> Decimal:
    """Expected minus settled, computed here rather than believed from a model.

    A model may describe a difference in prose, but no application decision
    rests on its arithmetic. Where a number matters, it is computed in Python
    with Decimal, the same definition the financial core uses.
    """
    return expected - settled
