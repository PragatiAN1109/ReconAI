"""The deterministic gate between an AI result and a human reviewer.

This module contains no model call, no network access and no randomness. Given a
validated result it returns the same decision every time, which is what makes it
auditable: "why was this escalated?" has an answer that can be re-derived from
the stored recommendation months later.

The model never reaches this code. It proposes an explanation; this decides what
happens to it.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.investigation_models import InvestigationResult, RootCauseClassification
from app.models import InvestigationStatus

logger = logging.getLogger(__name__)

#: Classifications that are an admission rather than a conclusion. No confidence
#: number makes these auto-routable: the model is reporting it does not know.
_NON_CONCLUSIVE = frozenset(
    {RootCauseClassification.INSUFFICIENT_EVIDENCE, RootCauseClassification.UNKNOWN}
)


@dataclass(frozen=True)
class GuardrailDecision:
    """What the policy decided, and the reason, in reviewer-readable terms.

    ``status`` is never COMPLETED. Nothing in this module can complete an
    investigation — only a human approving one does that.
    """

    status: InvestigationStatus
    reason: str

    @property
    def escalated(self) -> bool:
        return self.status is InvestigationStatus.ESCALATED


def evaluate(
    result: InvestigationResult,
    *,
    confidence_threshold: Decimal,
    minimum_evidence: int,
) -> GuardrailDecision:
    """Route a validated result to review or to escalation.

    Escalation is not a failure. It is the system declining to present a weak
    explanation as if it were a finding, and it is the expected outcome whenever
    the evidence does not carry the conclusion.

    The confidence compared here is the model's own self-report. It is not a
    calibrated probability and must not be read as one; it is used only as an
    ordering signal against a threshold an operator chose. That is also why a
    high number alone is not enough — a result must cite evidence too.
    """
    # Checked before confidence, deliberately: a model that reports it lacks
    # evidence has told us the answer regardless of how sure it sounds.
    if result.classification in _NON_CONCLUSIVE:
        return GuardrailDecision(
            InvestigationStatus.ESCALATED,
            f"The investigation concluded {result.classification.value}, "
            "which needs a human rather than a routed recommendation.",
        )

    if len(result.evidence) < minimum_evidence:
        return GuardrailDecision(
            InvestigationStatus.ESCALATED,
            f"Only {len(result.evidence)} verified evidence reference(s) support this "
            f"conclusion; the policy requires at least {minimum_evidence}.",
        )

    # Decimal on both sides, and the same value that will be stored. A
    # float-against-Decimal comparison would make the boundary case turn on
    # binary rounding, so a result could be routed one way and persisted with a
    # confidence that argues for the other.
    confidence = result.confidence_value
    if confidence < confidence_threshold:
        return GuardrailDecision(
            InvestigationStatus.ESCALATED,
            f"Reported confidence {confidence} is below the review "
            f"threshold {confidence_threshold}.",
        )

    return GuardrailDecision(
        InvestigationStatus.AWAITING_REVIEW,
        f"Reported confidence {confidence} meets the review threshold "
        f"{confidence_threshold} with {len(result.evidence)} verified evidence "
        "reference(s); awaiting human approval.",
    )
