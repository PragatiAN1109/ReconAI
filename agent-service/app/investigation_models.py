"""What an investigation is asked, and what it is allowed to answer.

The classification an investigation proposes is a different thing from the
discrepancy reconciliation detected, and the two enums are deliberately
separate. Reconciliation says ``AMOUNT_MISMATCH`` — two authoritative records
disagree. An investigation may later say ``PROCESSOR_FEE`` — here is why. The
first is a fact; the second is a conclusion requiring evidence and human review.
"""

import json
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.events import ExceptionType

# Same strictness as every other contract in this service: unexpected fields are
# rejected rather than ignored. A model inventing an extra field is exactly the
# drift worth hearing about.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

#: Upper bound on the two free-text fields a result carries.
#:
#: Declared here rather than inline so the Pydantic constraint and the JSON
#: Schema handed to the provider are the same number by construction. INV-1004
#: was caused by the two contracts disagreeing; a shared constant is the cheapest
#: way to stop that particular disagreement recurring.
NARRATIVE_MAX_LENGTH = 2000


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
    root_cause: str = Field(alias="rootCause", min_length=1, max_length=NARRATIVE_MAX_LENGTH)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recommended_action: str = Field(
        alias="recommendedAction", min_length=1, max_length=NARRATIVE_MAX_LENGTH
    )
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

    @property
    def confidence_value(self) -> Decimal:
        """Confidence as an exact decimal, for comparison and storage.

        The field itself stays a float because that is what arrives over JSON.
        Every decision and every write uses this instead, so the guardrail
        threshold comparison and the NUMERIC(5,4) column see the same value and
        a boundary case cannot turn on binary rounding.

        Via ``str`` rather than ``Decimal(float)``: 0.85 should become exactly
        ``0.85``, not the full binary expansion of the nearest double.

        This is a self-reported number, not a calibrated probability. Exactness
        here is about reproducibility, and says nothing about its meaning.
        """
        return Decimal(str(self.confidence)).quantize(Decimal("0.0001"))


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """The enum's members as strings, in declaration order.

    Generated rather than written out, so adding a classification cannot leave
    the provider's schema listing a stale set.
    """
    return [member.value for member in enum_class]


def result_input_schema() -> dict[str, Any]:
    """The JSON Schema for a final investigation result.

    Lives beside :class:`InvestigationResult` deliberately. This schema and that
    model describe the same contract to two different audiences — the provider
    and this application — and INV-1004 happened because they disagreed. Keeping
    them in one file, sharing one length constant and generating both enum
    domains from the enums themselves removes the classes of disagreement that
    a reader could not spot.

    Property names are the **aliases**, because that is what a model is asked to
    produce. ``populate_by_name`` means the model also accepts the snake_case
    field names, so this schema is narrower than Pydantic on that axis only —
    which is the safe direction.

    ``section`` is ``["string", "null"]`` rather than ``"string"``: Pydantic
    accepts an explicit ``null`` there, and a schema that refused one would be
    stricter than the contract it describes.

    **What this schema cannot do.** ``required`` is instruction to the model, not
    an API-side validator — INV-1004 omitted ``confidence`` while ``confidence``
    was already listed as required. Treat every constraint here as making a
    malformed result unlikely, never impossible. :class:`InvestigationResult` is
    the enforcement boundary and is the only one that actually rejects.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "classification": {
                "type": "string",
                "enum": _enum_values(RootCauseClassification),
            },
            "rootCause": {
                "type": "string",
                "minLength": 1,
                "maxLength": NARRATIVE_MAX_LENGTH,
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "sourceType": {
                            "type": "string",
                            "enum": _enum_values(EvidenceSource),
                        },
                        "reference": {"type": "string", "minLength": 1},
                        "section": {"type": ["string", "null"]},
                    },
                    "required": ["sourceType", "reference"],
                },
            },
            "recommendedAction": {
                "type": "string",
                "minLength": 1,
                "maxLength": NARRATIVE_MAX_LENGTH,
            },
            # Only true is valid. The Pydantic validator refuses false outright;
            # this states the same thing to the model rather than letting it
            # produce a result that is rejected after the fact.
            "requiresHumanApproval": {"type": "boolean", "enum": [True]},
        },
        "required": [
            "classification",
            "rootCause",
            "confidence",
            "evidence",
            "recommendedAction",
            "requiresHumanApproval",
        ],
    }


def describe_payload(payload: Mapping[str, Any] | None) -> str:
    """A log-safe fingerprint of a submitted result.

    Two scalars only. ``classification`` and ``confidence`` are enough to
    identify which conclusion was rejected and are not themselves financial
    detail; ``rootCause``, ``recommendedAction`` and the evidence list are
    deliberately excluded because they are.

    Reports the keys as ``null`` when absent, which is the useful signal when a
    required field was the thing missing — as it was for INV-1004.
    """
    if payload is None:
        return "null"
    return json.dumps(
        {key: payload.get(key) for key in ("classification", "confidence")},
        default=str,
    )


def difference(expected: Decimal, settled: Decimal) -> Decimal:
    """Expected minus settled, computed here rather than believed from a model.

    A model may describe a difference in prose, but no application decision
    rests on its arithmetic. Where a number matters, it is computed in Python
    with Decimal, the same definition the financial core uses.
    """
    return expected - settled
