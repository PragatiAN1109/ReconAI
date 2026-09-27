"""Tests for the scenario model and dataset validation.

A benchmark that accepts a malformed case reports numbers that look like
measurement and are not. These tests are the reason the dataset can be trusted.

No network, no provider, no credentials.
"""

import pytest

from app.events import ExceptionType
from app.investigation_models import EvidenceSource, RootCauseClassification

from evals.dataset import SCENARIOS
from evals.scenario import (
    Difficulty,
    ExpectedEvidence,
    OfflineScript,
    Scenario,
    validate_dataset,
)

T = RootCauseClassification


def scenario(**overrides) -> Scenario:
    base = {
        "scenario_id": "test-case",
        "title": "Test",
        "description": "Test scenario.",
        "exception_type": ExceptionType.AMOUNT_MISMATCH,
        "transaction": {
            "transactionId": "TX-T1",
            "merchantId": "M-1",
            "amount": "100.00",
            "expectedSettlementAmount": "100.00",
            "currency": "USD",
            "transactionType": "PURCHASE",
            "status": "POSTED",
            "transactionTimestamp": "2026-09-20T10:00:00Z",
            "createdAt": "2026-09-20T10:00:00Z",
        },
        "settlements": [
            {
                "settlementId": "SET-T1",
                "transactionId": "TX-T1",
                "processor": "P",
                "settledAmount": "90.00",
                "currency": "USD",
                "status": "COMPLETED",
                "settlementTimestamp": "2026-09-20T20:00:00Z",
            }
        ],
        "fee_rules": [],
        "expected_classification": T.PROCESSOR_FEE,
        "should_escalate": False,
        "rationale": "Because.",
        "difficulty": Difficulty.CLEAR,
        "offline": OfflineScript(classification=T.PROCESSOR_FEE, confidence=0.9),
    }
    return Scenario(**(base | overrides))


# ---------------------------------------------------------------------------
# The shipped dataset
# ---------------------------------------------------------------------------


def test_the_dataset_is_valid(policy_ids: set[str]) -> None:
    validate_dataset(SCENARIOS, policy_ids)


def test_the_dataset_has_no_duplicate_ids() -> None:
    ids = [s.scenario_id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_the_dataset_covers_every_implemented_classification() -> None:
    """A taxonomy value with no scenario is a value nothing measures."""
    covered = {s.expected_classification for s in SCENARIOS}
    assert covered == set(RootCauseClassification)


def test_the_dataset_covers_every_difficulty_band() -> None:
    covered = {s.difficulty for s in SCENARIOS}
    assert covered == set(Difficulty)


def test_the_dataset_contains_adversarial_cases() -> None:
    """Cases where the superficially obvious answer is wrong.

    Without these, a system that matches amounts to fee rules and stops would
    score well, which is precisely the failure mode worth detecting.
    """
    adversarial = [s for s in SCENARIOS if s.difficulty is Difficulty.ADVERSARIAL]
    assert len(adversarial) >= 8


def test_the_dataset_has_both_escalate_and_review_cases() -> None:
    """A dataset skewed entirely one way cannot measure escalation at all."""
    escalate = sum(1 for s in SCENARIOS if s.should_escalate)
    assert 5 <= escalate <= len(SCENARIOS) - 5


def test_every_scenario_explains_itself() -> None:
    for s in SCENARIOS:
        assert s.rationale.strip(), s.scenario_id
        assert len(s.rationale) > 40, f"{s.scenario_id}: rationale is too thin to review"


# ---------------------------------------------------------------------------
# Validation rejects bad cases
# ---------------------------------------------------------------------------


def test_duplicate_scenario_ids_are_rejected(policy_ids: set[str]) -> None:
    with pytest.raises(ValueError, match="duplicate scenario_id"):
        validate_dataset([scenario(), scenario()], policy_ids)


def test_an_unsupported_classification_is_rejected() -> None:
    with pytest.raises(ValueError):
        scenario(expected_classification="NOT_A_CLASSIFICATION")


def test_an_invalid_exception_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        scenario(exception_type="PROCESSOR_FEE")  # a root cause, never a detection


def test_evidence_referencing_a_nonexistent_record_is_rejected() -> None:
    with pytest.raises(ValueError, match="which no tool in this scenario could return"):
        scenario(
            expected_evidence=(
                ExpectedEvidence(
                    source_type=EvidenceSource.FEE_RULE, reference="FR-NOPE", why="x"
                ),
            )
        )


def test_evidence_referencing_a_missing_policy_is_rejected(policy_ids: set[str]) -> None:
    bad = scenario(
        expected_evidence=(
            ExpectedEvidence(
                source_type=EvidenceSource.POLICY_DOCUMENT, reference="POL-NOPE", why="x"
            ),
        )
    )
    with pytest.raises(ValueError, match="not in the corpus"):
        validate_dataset([bad], policy_ids)


def test_a_tool_both_required_and_forbidden_is_rejected() -> None:
    with pytest.raises(ValueError, match="both required and forbidden"):
        scenario(
            required_tools=frozenset({"get_fee_rules"}),
            forbidden_tools=frozenset({"get_fee_rules"}),
        )


def test_a_tool_both_optional_and_forbidden_is_rejected() -> None:
    with pytest.raises(ValueError, match="both optional and forbidden"):
        scenario(
            optional_tools=frozenset({"get_fee_rules"}),
            forbidden_tools=frozenset({"get_fee_rules"}),
        )


def test_an_unknown_tool_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tools"):
        scenario(required_tools=frozenset({"run_sql"}))


def test_an_unknown_tool_in_the_offline_script_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tools"):
        OfflineScript(
            tool_calls=("read_file",), classification=T.UNKNOWN, confidence=0.5
        )


def test_a_non_conclusive_classification_must_expect_escalation() -> None:
    """The guardrail always escalates these, so the alternative is unsatisfiable."""
    with pytest.raises(ValueError, match="guardrail always escalates"):
        scenario(expected_classification=T.INSUFFICIENT_EVIDENCE, should_escalate=False)
    with pytest.raises(ValueError, match="guardrail always escalates"):
        scenario(expected_classification=T.UNKNOWN, should_escalate=False)


def test_a_malformed_amount_is_rejected() -> None:
    bad = dict(scenario().transaction) | {"expectedSettlementAmount": "not-a-number"}
    with pytest.raises(ValueError, match="transaction payload is invalid"):
        scenario(transaction=bad)


def test_a_malformed_currency_is_rejected() -> None:
    bad = dict(scenario().transaction) | {"currency": "DOLLARS"}
    with pytest.raises(ValueError, match="transaction payload is invalid"):
        scenario(transaction=bad)


def test_a_settlement_for_another_transaction_is_rejected() -> None:
    stray = dict(scenario().settlements[0]) | {"transactionId": "TX-OTHER"}
    with pytest.raises(ValueError, match="references a different transaction"):
        scenario(settlements=[stray])


def test_a_missing_rationale_is_rejected() -> None:
    with pytest.raises(ValueError):
        scenario(rationale="")


def test_a_confidence_outside_zero_to_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        OfflineScript(classification=T.UNKNOWN, confidence=1.5)


def test_scenarios_are_frozen() -> None:
    """Truth must not be editable by the thing being measured."""
    with pytest.raises(ValueError):
        scenario().scenario_id = "changed"
