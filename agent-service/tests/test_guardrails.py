"""The deterministic routing policy.

No database, no model, no network. That is the point of the module: given a
result, the decision must be reproducible from the inputs alone, so these tests
need nothing but the inputs.
"""

from decimal import Decimal

import pytest

from app import guardrails
from app.investigation_models import InvestigationResult
from app.models import InvestigationStatus

THRESHOLD = Decimal("0.85")


def result(
    classification: str = "PROCESSOR_FEE",
    confidence: float = 0.9,
    evidence_count: int = 1,
) -> InvestigationResult:
    return InvestigationResult.model_validate(
        {
            "classification": classification,
            "rootCause": "A processing fee accounts for the difference.",
            "confidence": confidence,
            "evidence": [
                {"sourceType": "SETTLEMENT", "reference": f"SET-{8000 + index}"}
                for index in range(evidence_count)
            ],
            "recommendedAction": "Classify as a processor fee adjustment.",
            "requiresHumanApproval": True,
        }
    )


def evaluate(
    investigation_result: InvestigationResult,
    threshold: Decimal = THRESHOLD,
    minimum_evidence: int = 1,
):
    return guardrails.evaluate(
        investigation_result,
        confidence_threshold=threshold,
        minimum_evidence=minimum_evidence,
    )


def test_a_confident_well_evidenced_result_goes_to_a_human() -> None:
    decision = evaluate(result(confidence=0.9))

    assert decision.status is InvestigationStatus.AWAITING_REVIEW
    assert not decision.escalated


def test_a_low_confidence_result_is_escalated() -> None:
    decision = evaluate(result(confidence=0.4))

    assert decision.status is InvestigationStatus.ESCALATED
    assert "below the review threshold" in decision.reason


def test_confidence_exactly_at_the_threshold_passes() -> None:
    """The boundary is inclusive, and must not turn on binary rounding."""
    decision = evaluate(result(confidence=0.85))

    assert decision.status is InvestigationStatus.AWAITING_REVIEW


def test_confidence_just_below_the_threshold_is_escalated() -> None:
    decision = evaluate(result(confidence=0.8499))

    assert decision.status is InvestigationStatus.ESCALATED


@pytest.mark.parametrize("classification", ["INSUFFICIENT_EVIDENCE", "UNKNOWN"])
def test_a_non_conclusive_classification_is_escalated_however_confident(
    classification: str,
) -> None:
    """A model reporting that it does not know has answered the question.

    No confidence number makes "I could not determine this" routable as a
    finding, so the classification is checked before the number.
    """
    decision = evaluate(result(classification=classification, confidence=1.0))

    assert decision.status is InvestigationStatus.ESCALATED
    assert classification in decision.reason


def test_a_conclusion_citing_nothing_is_escalated() -> None:
    """High confidence with no evidence is exactly what grounding exists to catch."""
    decision = evaluate(result(confidence=0.99, evidence_count=0))

    assert decision.status is InvestigationStatus.ESCALATED
    assert "evidence" in decision.reason


def test_the_evidence_minimum_is_configurable() -> None:
    two_required = evaluate(result(evidence_count=1), minimum_evidence=2)
    assert two_required.status is InvestigationStatus.ESCALATED

    assert (
        evaluate(result(evidence_count=2), minimum_evidence=2).status
        is InvestigationStatus.AWAITING_REVIEW
    )


def test_the_threshold_is_configurable() -> None:
    moderate = result(confidence=0.7)

    assert evaluate(moderate, threshold=Decimal("0.6")).status is (
        InvestigationStatus.AWAITING_REVIEW
    )
    assert evaluate(moderate, threshold=Decimal("0.95")).status is (
        InvestigationStatus.ESCALATED
    )


def test_the_policy_never_completes_an_investigation() -> None:
    """Only a human can complete one. The guardrail has two outcomes, not three."""
    confidences = [index / 20 for index in range(21)]
    classifications = [
        "PROCESSOR_FEE",
        "PROCESSOR_DELAY",
        "DUPLICATE_PROCESSING",
        "CURRENCY_CONVERSION",
        "PROCESSOR_ERROR",
        "UNKNOWN",
        "INSUFFICIENT_EVIDENCE",
    ]

    outcomes = {
        evaluate(
            result(classification=classification, confidence=confidence, evidence_count=count)
        ).status
        for classification in classifications
        for confidence in confidences
        for count in (0, 1, 3)
    }

    assert outcomes <= {InvestigationStatus.AWAITING_REVIEW, InvestigationStatus.ESCALATED}
    assert InvestigationStatus.COMPLETED not in outcomes


def test_the_same_result_always_routes_the_same_way() -> None:
    """Determinism is the property that makes an escalation explainable later."""
    subject = result(confidence=0.86)

    decisions = {(evaluate(subject).status, evaluate(subject).reason) for _ in range(25)}

    assert len(decisions) == 1


def test_the_reason_quotes_the_threshold_that_was_applied() -> None:
    """An escalation must be re-derivable from what was stored with it."""
    decision = evaluate(result(confidence=0.5), threshold=Decimal("0.75"))

    assert "0.5" in decision.reason
    assert "0.75" in decision.reason


def test_a_float_threshold_boundary_cannot_drift() -> None:
    """0.1 + 0.2 arithmetic must not decide whether a human sees something.

    Confidence arrives as a JSON float. Converted through ``str`` it becomes
    the decimal the model actually wrote, so a value equal to the threshold
    compares equal rather than a hair under it.
    """
    for value in ("0.85", "0.9", "0.7", "0.3"):
        threshold = Decimal(value)
        decision = evaluate(result(confidence=float(value)), threshold=threshold)
        assert decision.status is InvestigationStatus.AWAITING_REVIEW, value
